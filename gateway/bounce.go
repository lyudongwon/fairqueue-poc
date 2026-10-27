package main

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"log"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync/atomic"
	"time"
)

// Cookie bounce: a request without a valid gateway cookie is answered by the
// gateway itself with a 302 back to the same URL plus a signed cookie. The
// bounce never reaches the upstream and never enters the queue, so a first
// visit does not wait behind a crawler flood. The retry carries the cookie and
// is scheduled as that session (probation applies as for any new session).
//
// Token: <id>.<issued unix s>.<hmac>, hmac = first 16 bytes of
// HMAC-SHA256(secret, id + "." + issued), hex. A forged or edited token fails
// validation and is treated as cookieless (bounced again), so a client cannot
// invent sessions or backdate one to skip probation.
const sidCookie = "fq_sid"

type bouncer struct {
	enabled bool
	secret  []byte
	maxAge  time.Duration
	// BOUNCE_ADAPTIVE=N (>0): only bounce while the anonymous queues hold >= N waiting requests
	// (and for 10 s after), so cookie-accepting crawlers are not pushed into the shared probation pool.
	adaptive int
	until    atomic.Int64
}

type backlogger interface{ anonBacklog() int }

func (b *bouncer) active(adm admitter, now time.Time) bool {
	if b.adaptive <= 0 {
		return true
	}
	if bl, ok := adm.(backlogger); ok && bl.anonBacklog() >= b.adaptive {
		b.until.Store(now.Add(10 * time.Second).UnixNano())
	}
	return now.UnixNano() < b.until.Load()
}

func newBouncer() *bouncer {
	b := &bouncer{enabled: os.Getenv("BOUNCE") == "1", maxAge: 30 * 24 * time.Hour}
	if !b.enabled {
		return b
	}
	if s := os.Getenv("BOUNCE_SECRET"); s != "" {
		b.secret = []byte(s)
	} else {
		b.secret = make([]byte, 32)
		if _, err := rand.Read(b.secret); err != nil {
			log.Fatal(err)
		}
	}
	b.adaptive = envInt("BOUNCE_ADAPTIVE", 0)
	log.Printf("cookie bounce enabled adaptive=%d", b.adaptive)
	return b
}

func (b *bouncer) sign(payload string) string {
	m := hmac.New(sha256.New, b.secret)
	m.Write([]byte(payload))
	return hex.EncodeToString(m.Sum(nil)[:16])
}

// mint returns a fresh signed token issued at now.
func (b *bouncer) mint(now time.Time) string {
	raw := make([]byte, 8)
	if _, err := rand.Read(raw); err != nil {
		log.Fatal(err)
	}
	payload := hex.EncodeToString(raw) + "." + strconv.FormatInt(now.Unix(), 10)
	return payload + "." + b.sign(payload)
}

// valid checks the signature and age. It returns the id (used as the session).
func (b *bouncer) valid(token string, now time.Time) (id string, issued time.Time, ok bool) {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return "", time.Time{}, false
	}
	payload := parts[0] + "." + parts[1]
	if !hmac.Equal([]byte(parts[2]), []byte(b.sign(payload))) {
		return "", time.Time{}, false
	}
	sec, err := strconv.ParseInt(parts[1], 10, 64)
	if err != nil {
		return "", time.Time{}, false
	}
	issued = time.Unix(sec, 0)
	if issued.After(now.Add(time.Minute)) || now.Sub(issued) > b.maxAge {
		return "", time.Time{}, false
	}
	return parts[0], issued, true
}

// session returns the validated session id from the request, or "".
func (b *bouncer) session(r *http.Request, now time.Time) string {
	c, err := r.Cookie(sidCookie)
	if err != nil {
		return ""
	}
	id, _, ok := b.valid(c.Value, now)
	if !ok {
		return ""
	}
	return id
}

// exempt reports requests that must not be bounced: anything a cookie-less
// client legitimately sends (API calls, git over HTTP, static assets, health).
func exempt(r *http.Request) bool {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		return true
	}
	p := r.URL.Path
	for _, pre := range []string{"/api/", "/assets/", "/avatars/", "/-/", "/healthz"} {
		if strings.HasPrefix(p, pre) {
			return true
		}
	}
	return strings.Contains(p, ".git/") || strings.HasSuffix(p, "/info/refs")
}

// bounce answers r with a 302 to the same URL and a freshly signed cookie.
func (b *bouncer) bounce(w http.ResponseWriter, r *http.Request, now time.Time) {
	http.SetCookie(w, &http.Cookie{
		Name: sidCookie, Value: b.mint(now), Path: "/", HttpOnly: true,
		SameSite: http.SameSiteLaxMode, MaxAge: int(b.maxAge.Seconds()),
	})
	http.Redirect(w, r, r.URL.RequestURI(), http.StatusFound)
}
