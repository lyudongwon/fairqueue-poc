package main

import (
	"context"
	"sync"
	"testing"
	"time"
)

func TestFifoSemOrderAndCap(t *testing.T) {
	s := newFifoSem(1) // one slot => grants are strictly serialized
	r1, _ := s.admit(context.Background())
	var mu sync.Mutex
	var order []int
	var wg sync.WaitGroup
	for i := 0; i < 5; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			rel, st := s.admit(context.Background())
			if st != 0 {
				t.Errorf("unexpected status %d", st)
				return
			}
			mu.Lock()
			order = append(order, i)
			mu.Unlock()
			rel()
		}(i)
		time.Sleep(5 * time.Millisecond) // enqueue in order
	}
	r1()
	wg.Wait()
	for i, v := range order {
		if v != i {
			t.Fatalf("not FIFO: %v", order)
		}
	}
	if s.free != 1 {
		t.Fatalf("free=%d want 1", s.free)
	}
}

func TestFifoSemCancel(t *testing.T) {
	s := newFifoSem(1)
	rel, _ := s.admit(context.Background())
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	if _, st := s.admit(ctx); st != 499 {
		t.Fatalf("status %d want 499", st)
	}
	rel()
	if s.free != 1 || s.q.Len() != 0 {
		t.Fatalf("free=%d q=%d", s.free, s.q.Len())
	}
}

func TestIPLimiter(t *testing.T) {
	l := &ipLimiter{rate: 2, burst: 2, buckets: map[string]*bucket{}}
	ok := 0
	for i := 0; i < 10; i++ {
		if _, st := l.admit(context.Background(), "1.2.3.4", false); st == 0 {
			ok++
		}
	}
	if ok != 2 {
		t.Fatalf("burst ok=%d want 2", ok)
	}
	if _, st := l.admit(context.Background(), "5.6.7.8", false); st != 0 {
		t.Fatal("other IP limited")
	}
}

func TestRouteOf(t *testing.T) {
	cases := map[string]string{
		"/poc/redis": "home", "/poc/redis/src/branch/unstable/README.md": "src",
		"/poc/redis/commits/branch/unstable": "commits", "/poc/redis/commit/abc": "commit",
		"/poc/redis/blame/commit/abc/x.c": "blame", "/poc/redis/compare/a...b": "compare",
		"/user/login": "other", "/api/healthz": "other",
	}
	for p, want := range cases {
		if got := routeOf(p); got != want {
			t.Errorf("%s: %s want %s", p, got, want)
		}
	}
}
