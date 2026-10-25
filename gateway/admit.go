package main

import (
	"container/list"
	"context"
	"log"
	"os"
	"strconv"
	"sync"
	"time"
)

const maxWait = 30 * time.Second // FIFO wait cap; beyond it the request gets 503

func envInt(k string, def int) int {
	if v, err := strconv.Atoi(os.Getenv(k)); err == nil {
		return v
	}
	return def
}

func envFloat(k string, def float64) float64 {
	if v, err := strconv.ParseFloat(os.Getenv(k), 64); err == nil {
		return v
	}
	return def
}

func newAdmitter(mode string) admitter {
	switch mode {
	case "none":
		return passThrough{}
	case "ratelimit-ip":
		rl := &ipLimiter{rate: envFloat("RATE_PER_IP", 2), burst: envFloat("BURST_PER_IP", 2), buckets: map[string]*bucket{}}
		go rl.evictLoop()
		log.Printf("ratelimit-ip rate=%.1f/s burst=%.0f", rl.rate, rl.burst)
		return rl
	case "global-cap":
		n := envInt("GLOBAL_N", 4)
		log.Printf("global-cap N=%d", n)
		return globalCap{newFifoSem(n)}
	case "anon-cap":
		n, m := envInt("ANON_N", 2), envInt("COOKIE_M", 4)
		log.Printf("anon-cap anon N=%d cookie M=%d", n, m)
		return anonCap{anon: newFifoSem(n), cookie: newFifoSem(m)}
	case "fq-cost", "fq-cost-probation":
		var probation time.Duration
		if mode == "fq-cost-probation" {
			probation = time.Duration(envInt("PROBATION_S", 600)) * time.Second
		}
		q := newFairQueue(envInt("FQ_SLOTS", 4), probation)
		q.anonSplit = os.Getenv("ANON_SPLIT")
		if q.anonSplit != "" && q.anonSplit != "route" && q.anonSplit != "class" {
			log.Fatalf("unknown ANON_SPLIT %q (want \"\", route or class)", q.anonSplit)
		}
		log.Printf("%s slots=%d probation=%s anon_split=%q", mode, q.free, probation, q.anonSplit)
		return q
	}
	log.Fatalf("unknown MODE %q", mode)
	return nil
}

// --- ratelimit-ip: token bucket per client IP, 429 when empty ---

type bucket struct {
	tokens float64
	last   time.Time
}

type ipLimiter struct {
	mu          sync.Mutex
	rate, burst float64
	buckets     map[string]*bucket
}

func (l *ipLimiter) admit(_ context.Context, ri reqInfo) (func(time.Duration), int, string) {
	ip := ri.ip
	now := time.Now()
	l.mu.Lock()
	defer l.mu.Unlock()
	b := l.buckets[ip]
	if b == nil {
		b = &bucket{tokens: l.burst, last: now}
		l.buckets[ip] = b
	}
	b.tokens = min(l.burst, b.tokens+now.Sub(b.last).Seconds()*l.rate)
	b.last = now
	if b.tokens < 1 {
		return nil, 429, ""
	}
	b.tokens--
	return noop, 0, ""
}

// evictLoop drops buckets idle long enough to be full again (rotating IPs would grow the map forever).
func (l *ipLimiter) evictLoop() {
	idle := time.Duration(l.burst/l.rate*float64(time.Second)) + time.Second
	for range time.Tick(10 * time.Second) {
		l.mu.Lock()
		for ip, b := range l.buckets {
			if time.Since(b.last) > idle {
				delete(l.buckets, ip)
			}
		}
		l.mu.Unlock()
	}
}

// --- FIFO counting semaphore with bounded wait ---

type fifoSem struct {
	mu   sync.Mutex
	free int
	q    list.List // of chan struct{}
}

func newFifoSem(n int) *fifoSem { return &fifoSem{free: n} }

// acquire returns 0 when a slot is held, 503 after maxWait, 499 if the client left.
func (s *fifoSem) acquire(ctx context.Context) int {
	s.mu.Lock()
	if s.free > 0 && s.q.Len() == 0 {
		s.free--
		s.mu.Unlock()
		return 0
	}
	ch := make(chan struct{})
	el := s.q.PushBack(ch)
	s.mu.Unlock()

	timer := time.NewTimer(maxWait)
	defer timer.Stop()
	status := 0
	select {
	case <-ch:
		return 0
	case <-timer.C:
		status = 503
	case <-ctx.Done():
		status = 499
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	select {
	case <-ch: // granted while we were giving up: pass the slot on
		s.releaseLocked()
	default:
		s.q.Remove(el)
	}
	return status
}

func (s *fifoSem) release() {
	s.mu.Lock()
	s.releaseLocked()
	s.mu.Unlock()
}

// releaseLocked hands the slot straight to the oldest waiter, if any.
func (s *fifoSem) releaseLocked() {
	if front := s.q.Front(); front != nil {
		s.q.Remove(front)
		close(front.Value.(chan struct{}))
		return
	}
	s.free++
}

func (s *fifoSem) admit(ctx context.Context) (func(time.Duration), int, string) {
	if st := s.acquire(ctx); st != 0 {
		return nil, st, ""
	}
	return func(time.Duration) { s.release() }, 0, ""
}

// --- global-cap: all requests share N slots ---

type globalCap struct{ sem *fifoSem }

func (g globalCap) admit(ctx context.Context, _ reqInfo) (func(time.Duration), int, string) {
	return g.sem.admit(ctx)
}

// --- anon-cap: cookieless requests share N slots, cookie holders get their own M ---

type anonCap struct{ anon, cookie *fifoSem }

func (a anonCap) admit(ctx context.Context, ri reqInfo) (func(time.Duration), int, string) {
	if ri.session != "" {
		return a.cookie.admit(ctx)
	}
	return a.anon.admit(ctx)
}
