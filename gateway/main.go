// Minimal reverse proxy in front of Gitea. MODE selects the admission policy.
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"os/signal"
	"regexp"
	"strings"
	"syscall"
	"time"
)

const sessionCookie = "i_like_gitea" // verified in stage 1 (DECISIONS.md)

var routes = []struct {
	name string
	re   *regexp.Regexp
}{
	{"blame", regexp.MustCompile(`^/[^/]+/[^/]+/blame/`)},
	{"compare", regexp.MustCompile(`^/[^/]+/[^/]+/compare/`)},
	{"commits", regexp.MustCompile(`^/[^/]+/[^/]+/commits/`)},
	{"commit", regexp.MustCompile(`^/[^/]+/[^/]+/commit/`)},
	{"src", regexp.MustCompile(`^/[^/]+/[^/]+/src/`)},
	{"home", regexp.MustCompile(`^/[^/]+/[^/]+/?$`)},
}

var reserved = map[string]bool{"api": true, "user": true, "explore": true, "assets": true, "-": true, "avatars": true, "repo": true}

func routeOf(path string) string {
	if first, _, _ := strings.Cut(strings.TrimPrefix(path, "/"), "/"); reserved[first] {
		return "other"
	}
	for _, r := range routes {
		if r.re.MatchString(path) {
			return r.name
		}
	}
	return "other"
}

func clientIP(r *http.Request) string {
	if xff := r.Header.Get("X-Forwarded-For"); xff != "" {
		first, _, _ := strings.Cut(xff, ",")
		return strings.TrimSpace(first)
	}
	host, _, _ := net.SplitHostPort(r.RemoteAddr)
	return host
}

// sessionOf returns the Gitea session cookie value, or "" if absent.
func sessionOf(r *http.Request) string {
	if c, err := r.Cookie(sessionCookie); err == nil {
		return c.Value
	}
	return ""
}

func short(s string) string {
	if len(s) > 8 {
		return s[:8]
	}
	return s
}

// entry is one JSON line per request.
type entry struct {
	TS         int64   `json:"ts"` // arrival, unix ms
	Mode       string  `json:"mode"`
	Cookie     bool    `json:"cookie"`
	Route      string  `json:"route"`
	IP         string  `json:"ip"`
	WaitMs     float64 `json:"wait_ms"`
	TTFBMs     float64 `json:"ttfb_ms"`
	UpstreamMs float64 `json:"upstream_ms"`
	Status     int     `json:"status"`
	NewSession bool    `json:"new_session"`
	Sess       string  `json:"sess,omitempty"`      // first 8 chars of the session cookie
	Principal  string  `json:"principal,omitempty"` // scheduling principal (fq modes)
}

type ctxKey struct{}

type reqInfo struct {
	ip      string
	session string // cookie value, "" if none
	route   string // route type from routeOf (blame, compare, home, ...)
}

// admitter decides whether/when a request may reach Gitea. It returns a
// release func (called with the request's upstream time), or a non-zero
// status to reject with, plus the principal it scheduled the request as.
type admitter interface {
	admit(ctx context.Context, ri reqInfo) (release func(time.Duration), status int, principal string)
}

// registrar is implemented by admitters that accept pre-aged sessions (/seen).
type registrar interface {
	register(session string, age time.Duration)
}

func noop(time.Duration) {}

type passThrough struct{}

func (passThrough) admit(context.Context, reqInfo) (func(time.Duration), int, string) {
	return noop, 0, ""
}

func ms(d time.Duration) float64 { return float64(d.Microseconds()) / 1000 }

func main() {
	mode := getenv("MODE", "none")
	upstream, _ := url.Parse(getenv("UPSTREAM", "http://gitea:3000"))
	logs := make(chan entry, 1<<16)
	done := startLogger(getenv("LOG_PATH", "/dev/stdout"), logs)
	adm := newAdmitter(mode)

	proxy := httputil.NewSingleHostReverseProxy(upstream)
	proxy.Transport = &http.Transport{MaxIdleConns: 4096, MaxIdleConnsPerHost: 4096, IdleConnTimeout: 90 * time.Second, DisableCompression: true}
	proxy.ModifyResponse = func(resp *http.Response) error {
		e := resp.Request.Context().Value(ctxKey{}).(*reqState)
		e.ttfb = time.Since(e.start)
		for _, c := range resp.Cookies() {
			if c.Name == sessionCookie {
				e.newSession = true
			}
		}
		return nil
	}
	proxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		if r.Context().Err() != nil {
			w.WriteHeader(499) // client went away
			return
		}
		w.WriteHeader(http.StatusBadGateway)
	}

	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		arrival := time.Now()
		sess := sessionOf(r)
		e := entry{TS: arrival.UnixMilli(), Mode: mode, Cookie: sess != "", Route: routeOf(r.URL.Path), IP: clientIP(r), Sess: short(sess)}
		rw := &statusWriter{ResponseWriter: w, status: 200}
		release, reject, principal := adm.admit(r.Context(), reqInfo{ip: e.IP, session: sess, route: e.Route})
		e.Principal = principal
		e.WaitMs = ms(time.Since(arrival))
		if reject != 0 {
			if reject == 429 || reject == 503 {
				rw.Header().Set("Retry-After", "1")
			}
			rw.WriteHeader(reject)
		} else {
			st := &reqState{start: time.Now()}
			proxy.ServeHTTP(rw, r.WithContext(context.WithValue(r.Context(), ctxKey{}, st)))
			release(time.Since(st.start))
			e.UpstreamMs, e.TTFBMs, e.NewSession = ms(time.Since(st.start)), ms(st.ttfb), st.newSession
		}
		e.Status = rw.status
		logs <- e
	})

	if reg, ok := adm.(registrar); ok {
		go serveAdmin(reg)
	}
	srv := &http.Server{Addr: ":8080", Handler: handler, ReadHeaderTimeout: 10 * time.Second}
	go func() {
		sig := make(chan os.Signal, 1)
		signal.Notify(sig, syscall.SIGTERM, syscall.SIGINT)
		<-sig
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		srv.Shutdown(ctx)
	}()
	log.Printf("gateway mode=%s upstream=%s", mode, upstream)
	if err := srv.ListenAndServe(); err != http.ErrServerClosed {
		log.Fatal(err)
	}
	close(logs)
	<-done
}

// serveAdmin exposes POST /seen?session=...&age_s=... on :9090 (compose network
// only) so the human simulator can register its cookies as long-established.
func serveAdmin(reg registrar) {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /seen", func(w http.ResponseWriter, r *http.Request) {
		age, err := time.ParseDuration(r.URL.Query().Get("age_s") + "s")
		sess := r.URL.Query().Get("session")
		if err != nil || sess == "" {
			http.Error(w, "need session and age_s", http.StatusBadRequest)
			return
		}
		reg.register(sess, age)
		w.WriteHeader(http.StatusNoContent)
	})
	log.Fatal(http.ListenAndServe(":9090", mux))
}

type reqState struct {
	start      time.Time
	ttfb       time.Duration
	newSession bool
}

type statusWriter struct {
	http.ResponseWriter
	status int
}

func (s *statusWriter) WriteHeader(code int) { s.status = code; s.ResponseWriter.WriteHeader(code) }
func (s *statusWriter) Flush() {
	if f, ok := s.ResponseWriter.(http.Flusher); ok {
		f.Flush()
	}
}

// startLogger writes JSON lines with a buffered writer, flushed every second.
func startLogger(path string, in <-chan entry) <-chan struct{} {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		log.Fatal(err)
	}
	done := make(chan struct{})
	go func() {
		defer close(done)
		bw := bufio.NewWriterSize(f, 1<<20)
		enc := json.NewEncoder(bw)
		tick := time.NewTicker(time.Second)
		defer tick.Stop()
		for {
			select {
			case e, ok := <-in:
				if !ok {
					bw.Flush()
					f.Close()
					return
				}
				enc.Encode(e)
			case <-tick.C:
				bw.Flush()
			}
		}
	}()
	return done
}

func getenv(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}
