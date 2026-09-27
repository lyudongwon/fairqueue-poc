package main

import (
	"container/list"
	"context"
	"sync"
	"time"
)

// fairQueue is a cost-based fair queue over a fixed number of upstream slots.
// A freed slot goes to the waiting principal with the least accumulated
// upstream time (ms); within a principal, requests are FIFO.
//
// Principals: a session cookie is its own principal; cookieless requests
// share "anon". With probation > 0, sessions first seen less than probation
// ago all share "new" (their cost still accrues per session, and carries over
// when the session graduates to its own principal).
//
// Charging: a principal is charged an estimate at dispatch (its own mean
// upstream time so far, else the global mean, else defaultEstMs) and corrected
// to the actual time at release. Without this, requests still in flight
// would cost nothing and one principal could take every free slot at once.
const defaultEstMs = 100

type fqWaiter struct {
	ch        chan struct{}
	principal string
	est       float64
	seq       uint64
}

type fairQueue struct {
	mu        sync.Mutex
	free      int
	probation time.Duration
	cost      map[string]float64 // principal -> charged ms
	sessCost  map[string]float64 // session -> actual ms
	sum, n    map[string]float64 // principal -> actual ms / completed requests
	gsum, gn  float64
	queues    map[string]*list.List // principal -> FIFO of *fqWaiter
	waiting   int
	seq       uint64
	firstSeen map[string]time.Time
}

func newFairQueue(slots int, probation time.Duration) *fairQueue {
	return &fairQueue{
		free: slots, probation: probation,
		cost: map[string]float64{}, sessCost: map[string]float64{},
		sum: map[string]float64{}, n: map[string]float64{},
		queues: map[string]*list.List{}, firstSeen: map[string]time.Time{},
	}
}

func (q *fairQueue) register(session string, age time.Duration) {
	q.mu.Lock()
	q.firstSeen[session] = time.Now().Add(-age)
	q.mu.Unlock()
}

// principalLocked returns the scheduling key and its log label.
func (q *fairQueue) principalLocked(session string) (key, label string) {
	if session == "" {
		return "anon", "anon"
	}
	fs, ok := q.firstSeen[session]
	if !ok {
		fs = time.Now()
		q.firstSeen[session] = fs
	}
	if q.probation > 0 && time.Since(fs) < q.probation {
		return "new", "new"
	}
	key = "s:" + session
	if _, ok := q.cost[key]; !ok {
		q.cost[key] = q.sessCost[session] // graduation: keep what it already used
	}
	return key, "s:" + short(session)
}

func (q *fairQueue) estimateLocked(p string) float64 {
	if q.n[p] > 0 {
		return q.sum[p] / q.n[p]
	}
	if q.gn > 0 {
		return q.gsum / q.gn
	}
	return defaultEstMs
}

func (q *fairQueue) admit(ctx context.Context, ri reqInfo) (func(time.Duration), int, string) {
	q.mu.Lock()
	p, label := q.principalLocked(ri.session)
	est := q.estimateLocked(p)
	if q.free > 0 && q.waiting == 0 {
		q.free--
		q.cost[p] += est
		q.mu.Unlock()
		return q.releaser(p, ri.session, est), 0, label
	}
	q.seq++
	w := &fqWaiter{ch: make(chan struct{}), principal: p, est: est, seq: q.seq}
	l := q.queues[p]
	if l == nil {
		l = list.New()
		q.queues[p] = l
	}
	el := l.PushBack(w)
	q.waiting++
	q.mu.Unlock()

	timer := time.NewTimer(maxWait)
	defer timer.Stop()
	status := 0
	select {
	case <-w.ch:
		return q.releaser(p, ri.session, est), 0, label
	case <-timer.C:
		status = 503
	case <-ctx.Done():
		status = 499
	}
	q.mu.Lock()
	defer q.mu.Unlock()
	select {
	case <-w.ch: // granted while giving up: refund and pass the slot on
		q.cost[p] -= est
		q.dispatchLocked()
	default:
		l.Remove(el)
		q.waiting--
	}
	return nil, status, label
}

func (q *fairQueue) releaser(p, session string, est float64) func(time.Duration) {
	return func(d time.Duration) {
		a := ms(d)
		q.mu.Lock()
		q.cost[p] += a - est
		if session != "" {
			q.sessCost[session] += a
		}
		q.sum[p] += a
		q.n[p]++
		q.gsum += a
		q.gn++
		q.dispatchLocked()
		q.mu.Unlock()
	}
}

// dispatchLocked hands a freed slot to the cheapest waiting principal
// (ties: oldest head-of-line request), or returns it to the free pool.
func (q *fairQueue) dispatchLocked() {
	var best *list.List
	var bestW *fqWaiter
	for p, l := range q.queues {
		if l.Len() == 0 {
			continue
		}
		w := l.Front().Value.(*fqWaiter)
		if bestW == nil || q.cost[p] < q.cost[bestW.principal] ||
			(q.cost[p] == q.cost[bestW.principal] && w.seq < bestW.seq) {
			best, bestW = l, w
		}
	}
	if bestW == nil {
		q.free++
		return
	}
	best.Remove(best.Front())
	q.waiting--
	q.cost[bestW.principal] += bestW.est
	close(bestW.ch)
}
