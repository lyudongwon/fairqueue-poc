package main

import (
	"context"
	"testing"
	"time"
)

func TestFairQueueCheapestFirst(t *testing.T) {
	q := newFairQueue(1, 0)
	rel, _, _ := q.admit(context.Background(), reqInfo{session: "heavy"})
	rel(500 * time.Millisecond) // heavy has used 500 ms
	hold, _, _ := q.admit(context.Background(), reqInfo{session: "heavy"})

	order := make(chan string, 2)
	go func() {
		r, _, _ := q.admit(context.Background(), reqInfo{session: "heavy"})
		order <- "heavy"
		r(0)
	}()
	time.Sleep(10 * time.Millisecond) // heavy queues first
	go func() {
		r, _, _ := q.admit(context.Background(), reqInfo{session: "light"})
		order <- "light"
		r(0)
	}()
	time.Sleep(10 * time.Millisecond)
	hold(100 * time.Millisecond)
	if first := <-order; first != "light" {
		t.Fatalf("first=%s, want light (cheaper principal) despite arriving later", first)
	}
	<-order
	if q.free != 1 || q.waiting != 0 {
		t.Fatalf("free=%d waiting=%d", q.free, q.waiting)
	}
}

func TestFairQueueAnonAndProbation(t *testing.T) {
	q := newFairQueue(4, 50*time.Millisecond)
	q.register("old", time.Hour)
	for _, c := range []struct{ sess, want string }{
		{"", "anon"}, {"fresh-1", "new"}, {"fresh-2", "new"}, {"old", "s:old"},
	} {
		rel, st, label := q.admit(context.Background(), reqInfo{session: c.sess})
		if st != 0 || label != c.want {
			t.Fatalf("session %q: label=%s status=%d, want %s", c.sess, label, st, c.want)
		}
		rel(10 * time.Millisecond)
	}
	// fresh-1 graduates after probation and keeps the cost it used as "new".
	time.Sleep(60 * time.Millisecond)
	rel, _, label := q.admit(context.Background(), reqInfo{session: "fresh-1"})
	if label != "s:fresh-1" {
		t.Fatalf("label=%s after probation", label)
	}
	rel(0)
	if c := q.cost["s:fresh-1"]; c < 9.9 || c > 10.1 {
		t.Fatalf("graduated cost=%.2f, want ≈10 carried over", c)
	}
}

func TestAnonSplit(t *testing.T) {
	for _, c := range []struct{ split, route, want string }{
		{"", "blame", "anon"}, {"", "home", "anon"},
		{"route", "blame", "anon:blame"}, {"route", "home", "anon:home"},
		{"class", "blame", "anon:heavy"}, {"class", "compare", "anon:heavy"},
		{"class", "commit", "anon:light"}, {"class", "home", "anon:light"}, {"class", "other", "anon:light"},
	} {
		q := newFairQueue(4, 0)
		q.anonSplit = c.split
		rel, st, label := q.admit(context.Background(), reqInfo{route: c.route})
		if st != 0 || label != c.want {
			t.Fatalf("split=%q route=%q: label=%s status=%d, want %s", c.split, c.route, label, st, c.want)
		}
		rel(time.Millisecond)
	}
	// Cookie holders are unaffected by the split.
	q := newFairQueue(4, 0)
	q.anonSplit = "route"
	rel, _, label := q.admit(context.Background(), reqInfo{session: "abcdefghij", route: "blame"})
	if label != "s:abcdefgh" {
		t.Fatalf("session label=%s", label)
	}
	rel(0)
}

// With a split, a cheap route's anon principal goes ahead of an expensive one even
// though the expensive requests queued first.
func TestAnonSplitOrdersByRouteCost(t *testing.T) {
	q := newFairQueue(1, 0)
	q.anonSplit = "route"
	rel, _, _ := q.admit(context.Background(), reqInfo{route: "blame"})
	rel(800 * time.Millisecond) // anon:blame has used 800 ms
	hold, _, _ := q.admit(context.Background(), reqInfo{route: "blame"})

	order := make(chan string, 2)
	go func() {
		r, _, _ := q.admit(context.Background(), reqInfo{route: "blame"})
		order <- "blame"
		r(0)
	}()
	time.Sleep(10 * time.Millisecond)
	go func() {
		r, _, _ := q.admit(context.Background(), reqInfo{route: "home"})
		order <- "home"
		r(0)
	}()
	time.Sleep(10 * time.Millisecond)
	hold(100 * time.Millisecond)
	if first := <-order; first != "home" {
		t.Fatalf("first=%s, want home (cheaper anon principal) despite arriving later", first)
	}
	<-order
}

// LIFO inside an anonymous principal: the newest waiter goes first; cookie holders stay FIFO.
func TestAnonLIFO(t *testing.T) {
	q := newFairQueue(1, 0)
	q.anonOrder = "lifo"
	hold, _, _ := q.admit(context.Background(), reqInfo{route: "commit"})
	order := make(chan string, 3)
	enqueue := func(name string) {
		go func() {
			r, _, _ := q.admit(context.Background(), reqInfo{route: "commit"})
			order <- name
			r(0)
		}()
		time.Sleep(10 * time.Millisecond)
	}
	enqueue("old")
	enqueue("mid")
	enqueue("new")
	hold(time.Millisecond)
	if first := <-order; first != "new" {
		t.Fatalf("first=%s, want new (LIFO)", first)
	}
	<-order
	<-order
	time.Sleep(20 * time.Millisecond)
	q.mu.Lock()
	if q.free != 1 || q.waiting != 0 {
		t.Fatalf("free=%d waiting=%d", q.free, q.waiting)
	}
	q.mu.Unlock()

	// default stays FIFO
	q = newFairQueue(1, 0)
	hold, _, _ = q.admit(context.Background(), reqInfo{route: "commit"})
	order = make(chan string, 3)
	enqueue = func(name string) {
		go func() {
			r, _, _ := q.admit(context.Background(), reqInfo{route: "commit"})
			order <- name
			r(0)
		}()
		time.Sleep(10 * time.Millisecond)
	}
	enqueue("old")
	enqueue("new")
	hold(time.Millisecond)
	if first := <-order; first != "old" {
		t.Fatalf("first=%s, want old (FIFO)", first)
	}
	<-order
}

// Heavy cap: with cap 1 only one blame/compare request holds a slot; the rest wait
// even though slots are free, and light requests use the free slots at once.
func TestHeavyCap(t *testing.T) {
	q := newFairQueue(3, 0)
	q.anonSplit = "route"
	q.heavyCap = 1
	h1, st, _ := q.admit(context.Background(), reqInfo{route: "blame"})
	if st != 0 {
		t.Fatalf("first heavy status %d", st)
	}
	got := make(chan string, 2)
	go func() {
		r, _, _ := q.admit(context.Background(), reqInfo{route: "compare"})
		got <- "heavy2"
		r(0)
	}()
	time.Sleep(20 * time.Millisecond)
	select {
	case <-got:
		t.Fatal("second heavy request must wait while cap is reached")
	default:
	}
	l1, st, _ := q.admit(context.Background(), reqInfo{route: "home"})
	if st != 0 {
		t.Fatalf("light status %d", st)
	}
	l1(time.Millisecond)
	select {
	case <-got:
		t.Fatal("heavy2 got in before the first heavy finished")
	default:
	}
	h1(time.Millisecond)
	select {
	case <-got:
	case <-time.After(time.Second):
		t.Fatal("heavy2 not served after the first heavy finished")
	}
	time.Sleep(20 * time.Millisecond)
	q.mu.Lock()
	defer q.mu.Unlock()
	if q.inflightHeavy != 0 || q.waiting != 0 || q.free != 3 {
		t.Fatalf("inflightHeavy=%d waiting=%d free=%d", q.inflightHeavy, q.waiting, q.free)
	}
}

func TestFairQueueCancelWhileWaiting(t *testing.T) {
	q := newFairQueue(1, 0)
	rel, _, _ := q.admit(context.Background(), reqInfo{})
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	if _, st, _ := q.admit(ctx, reqInfo{session: "x"}); st != 499 {
		t.Fatalf("status %d want 499", st)
	}
	rel(0)
	if q.free != 1 || q.waiting != 0 {
		t.Fatalf("free=%d waiting=%d", q.free, q.waiting)
	}
}
