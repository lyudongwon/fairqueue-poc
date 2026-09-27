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
