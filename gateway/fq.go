package main

import (
	"container/list"
	"context"
	"strings"
	"sync"
	"time"
)

// fairQueue is a cost-based fair queue over a fixed number of upstream slots.
// A freed slot goes to the waiting principal with the least accumulated
// upstream time (ms); within a principal, requests are FIFO (anonOrder "lifo"
// serves the newest waiter of an anonymous principal first instead).
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
	heavy     bool // counts against heavyCap while in flight
}

type fairQueue struct {
	mu            sync.Mutex
	free          int
	probation     time.Duration
	anonSplit     string             // "", "route" or "class": how cookieless requests are grouped (see anonKey)
	anonOrder     string             // "" (FIFO) or "lifo": order inside an anonymous principal's queue
	heavyCap      int                // >0: at most this many anonymous heavy requests in flight (needs anonSplit)
	inflightHeavy int                // anonymous heavy requests currently holding a slot
	cost          map[string]float64 // principal -> charged ms
	sessCost      map[string]float64 // session -> actual ms
	sum, n        map[string]float64 // principal -> actual ms / completed requests
	gsum, gn      float64
	queues        map[string]*list.List // principal -> queue of *fqWaiter
	waiting       int
	seq           uint64
	firstSeen     map[string]time.Time
}

// anonKey names the principal of a cookieless request.
//
//	split ""      -> one shared "anon" (stage 7 behaviour)
//	split "route" -> one principal per route type ("anon:blame", "anon:home", ...)
//	split "class" -> two principals by expected cost: "anon:heavy" (blame, compare)
//	                 and "anon:light" (everything else)
//
// The split uses only the requested path, never who is asking.
func anonKey(split, route string) string {
	switch split {
	case "route":
		return "anon:" + route
	case "class":
		if isHeavyRoute(route) {
			return "anon:heavy"
		}
		return "anon:light"
	}
	return "anon"
}

// isHeavyRoute marks the route types whose requests cost an order of magnitude more
// than the rest (stage 1: blame ≈ 163 ms, compare ≈ 1544 ms median).
func isHeavyRoute(route string) bool { return route == "blame" || route == "compare" }

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
func (q *fairQueue) principalLocked(session, route string) (key, label string) {
	if session == "" {
		k := anonKey(q.anonSplit, route)
		return k, k
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

func (q *fairQueue) heavyBlockedLocked() bool {
	return q.heavyCap > 0 && q.inflightHeavy >= q.heavyCap
}

func (q *fairQueue) admit(ctx context.Context, ri reqInfo) (func(time.Duration), int, string) {
	q.mu.Lock()
	p, label := q.principalLocked(ri.session, ri.route)
	est := q.estimateLocked(p)
	heavy := q.heavyCap > 0 && ri.session == "" && isHeavyRoute(ri.route)
	if q.free > 0 && q.waiting == 0 && !(heavy && q.heavyBlockedLocked()) {
		q.free--
		if heavy {
			q.inflightHeavy++
		}
		q.cost[p] += est
		q.mu.Unlock()
		return q.releaser(p, ri.session, est, heavy), 0, label
	}
	q.seq++
	w := &fqWaiter{ch: make(chan struct{}), principal: p, est: est, seq: q.seq, heavy: heavy}
	l := q.queues[p]
	if l == nil {
		l = list.New()
		q.queues[p] = l
	}
	el := l.PushBack(w)
	q.waiting++
	q.drainLocked() // a free slot may be waiting for an eligible request (heavy cap)
	q.mu.Unlock()

	timer := time.NewTimer(maxWait)
	defer timer.Stop()
	status := 0
	select {
	case <-w.ch:
		return q.releaser(p, ri.session, est, heavy), 0, label
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
		if heavy {
			q.inflightHeavy--
		}
		q.free++
		q.drainLocked()
	default:
		l.Remove(el)
		q.waiting--
	}
	return nil, status, label
}

func (q *fairQueue) releaser(p, session string, est float64, heavy bool) func(time.Duration) {
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
		if heavy {
			q.inflightHeavy--
		}
		q.free++
		q.drainLocked()
		q.mu.Unlock()
	}
}

// headLocked is the waiter of l that would be served next within its principal.
func (q *fairQueue) headLocked(p string, l *list.List) *list.Element {
	if q.anonOrder == "lifo" && strings.HasPrefix(p, "anon") {
		return l.Back()
	}
	return l.Front()
}

// drainLocked hands free slots to the cheapest eligible waiting principal
// (ties: oldest head-of-line request) until no slot or no eligible waiter is left.
// A heavy waiter is not eligible while the heavy cap is reached.
func (q *fairQueue) drainLocked() {
	for q.free > 0 {
		var bestL *list.List
		var bestEl *list.Element
		var bestW *fqWaiter
		for p, l := range q.queues {
			if l.Len() == 0 {
				continue
			}
			el := q.headLocked(p, l)
			w := el.Value.(*fqWaiter)
			if w.heavy && q.heavyBlockedLocked() {
				continue
			}
			if bestW == nil || q.cost[p] < q.cost[bestW.principal] ||
				(q.cost[p] == q.cost[bestW.principal] && w.seq < bestW.seq) {
				bestL, bestEl, bestW = l, el, w
			}
		}
		if bestW == nil {
			return
		}
		bestL.Remove(bestEl)
		q.waiting--
		q.free--
		if bestW.heavy {
			q.inflightHeavy++
		}
		q.cost[bestW.principal] += bestW.est
		close(bestW.ch)
	}
}
