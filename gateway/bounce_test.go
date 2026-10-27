package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func testBouncer() *bouncer {
	return &bouncer{enabled: true, secret: []byte("test-secret"), maxAge: 24 * time.Hour}
}

func TestMintValid(t *testing.T) {
	b := testBouncer()
	now := time.Now()
	tok := b.mint(now)
	id, issued, ok := b.valid(tok, now)
	if !ok || id == "" || issued.Unix() != now.Unix() {
		t.Fatalf("valid token rejected: ok=%v id=%q issued=%v", ok, id, issued)
	}
}

func TestForgeryRejected(t *testing.T) {
	b := testBouncer()
	now := time.Now()
	tok := b.mint(now)
	parts := strings.Split(tok, ".")
	// backdate the issue time (to skip probation) keeping the old signature
	forged := parts[0] + ".1000000000." + parts[2]
	if _, _, ok := b.valid(forged, now); ok {
		t.Fatal("backdated token accepted")
	}
	if _, _, ok := b.valid("abc.123.deadbeef", now); ok {
		t.Fatal("made-up token accepted")
	}
	other := &bouncer{enabled: true, secret: []byte("other"), maxAge: 24 * time.Hour}
	if _, _, ok := other.valid(tok, now); ok {
		t.Fatal("token from another secret accepted")
	}
}

func TestExpiredRejected(t *testing.T) {
	b := testBouncer()
	tok := b.mint(time.Now().Add(-48 * time.Hour))
	if _, _, ok := b.valid(tok, time.Now()); ok {
		t.Fatal("expired token accepted")
	}
}

func TestBounceResponse(t *testing.T) {
	b := testBouncer()
	r := httptest.NewRequest("GET", "/poc/redis/blame/main/README.md?x=1", nil)
	w := httptest.NewRecorder()
	b.bounce(w, r, time.Now())
	if w.Code != http.StatusFound {
		t.Fatalf("status %d", w.Code)
	}
	if loc := w.Header().Get("Location"); loc != "/poc/redis/blame/main/README.md?x=1" {
		t.Fatalf("location %q", loc)
	}
	// the cookie it sets must validate and give back a session on retry
	var c *http.Cookie
	for _, ck := range w.Result().Cookies() {
		if ck.Name == sidCookie {
			c = ck
		}
	}
	if c == nil {
		t.Fatal("no cookie set")
	}
	r2 := httptest.NewRequest("GET", "/poc/redis", nil)
	r2.AddCookie(c)
	if b.session(r2, time.Now()) == "" {
		t.Fatal("retry with bounce cookie has no session")
	}
	if b.session(httptest.NewRequest("GET", "/poc/redis", nil), time.Now()) != "" {
		t.Fatal("cookieless request has a session")
	}
}

func TestExempt(t *testing.T) {
	cases := []struct {
		method, path string
		want         bool
	}{
		{"GET", "/poc/redis", false},
		{"GET", "/poc/redis/blame/main/a.go", false},
		{"POST", "/user/login", true},
		{"GET", "/api/v1/repos/x", true},
		{"GET", "/assets/css/index.css", true},
		{"GET", "/poc/redis.git/info/refs", true},
		{"GET", "/healthz", true},
	}
	for _, c := range cases {
		r := httptest.NewRequest(c.method, c.path, nil)
		if got := exempt(r); got != c.want {
			t.Errorf("%s %s: exempt=%v want %v", c.method, c.path, got, c.want)
		}
	}
}
