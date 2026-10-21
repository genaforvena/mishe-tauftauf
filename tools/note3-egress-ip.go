// egress-ip prints the address this device's traffic leaves from.
//
// The Note3 is an Android 5.0 phone with no HTTP client: /system/bin has ping
// and nothing else, and the plant's egress claim about this device has to be
// an observation rather than an inference. This is that observation: one
// request to an address-echo service, printed as one bare token.
//
// No DNS: Android 5.0 has no /etc/resolv.conf, Go's resolver would fall back
// to [::1]:53 (which answers nothing), and the property area that holds the
// phone's resolvers is not plain text on this ROM. So the request goes to an
// address literal -- Cloudflare's anycast 1.1.1.1, which reports the source
// address of the connection in its trace -- and the address is read out of
// that trace.
//
// Usage: egress-ip [url]   (default http://1.1.1.1/cdn-cgi/trace)
// Exit: 0 with the address on stdout, 1 with the reason on stderr.
package main

import (
	"crypto/tls"
	"crypto/x509"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"strings"
	"time"
)

// roots returns the phone's certificate authorities when they can be read.
// Android keeps them in /system/etc/security/cacerts, which Go can load; when
// it cannot, the connection is left unverified -- the answer this program
// reads is the source address of the connection, and the leg to it is a leg
// this plant does not trust either way.
func roots() *x509.CertPool {
	pool, err := x509.SystemCertPool()
	if err != nil || pool == nil {
		return nil
	}
	if len(pool.Subjects()) == 0 { //nolint:staticcheck // subjects are enough to tell an empty pool
		return nil
	}
	return pool
}

func main() {
	url := "http://1.1.1.1/cdn-cgi/trace"
	if len(os.Args) > 1 {
		url = os.Args[1]
	}
	tlsConfig := &tls.Config{RootCAs: roots()}
	if tlsConfig.RootCAs == nil {
		// No usable trust store on this ROM: the answer this program reads
		// is the source address of the connection itself, and this leg is
		// one the plant does not trust either way.
		tlsConfig.InsecureSkipVerify = true
	}
	client := &http.Client{
		Timeout:   15 * time.Second,
		Transport: &http.Transport{TLSClientConfig: tlsConfig},
	}
	resp, err := client.Get(url)
	if err != nil {
		fmt.Fprintln(os.Stderr, "request failed:", err)
		os.Exit(1)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 4096))
	if err != nil {
		fmt.Fprintln(os.Stderr, "read failed:", err)
		os.Exit(1)
	}
	text := string(body)
	token := strings.TrimSpace(text)
	if ip := net.ParseIP(token); ip != nil {
		fmt.Println(token)
		return
	}
	// A trace body: one "key=value" per line, of which "ip=" is the one.
	for _, line := range strings.Split(text, "\n") {
		value, ok := strings.CutPrefix(strings.TrimSpace(line), "ip=")
		if !ok {
			continue
		}
		if ip := net.ParseIP(strings.TrimSpace(value)); ip != nil {
			fmt.Println(strings.TrimSpace(value))
			return
		}
	}
	fmt.Fprintln(os.Stderr, "no address in the response")
	os.Exit(1)
}
