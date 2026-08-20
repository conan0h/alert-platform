package main

import (
	"flag"
	"fmt"
	"net"
	"net/http"
	"strings"

	"github.com/conanohara/alert-platform/internal/console"
)

// cmdServe starts the read-only operator console.
//
// The console is a second interface over the same engine the CLI uses; it
// adds no write path. It binds to loopback by default and stays there: for
// a remote target, port-forward over SSH — the same transport the platform
// already trusts for everything else — instead of exposing a listener.
func cmdServe(args []string) error {
	fs := flag.NewFlagSet("serve", flag.ExitOnError)
	var c common
	c.bind(fs)
	addr := fs.String("addr", "127.0.0.1:8600", "listen address (keep it on loopback; see below)")
	_ = fs.Parse(args)

	repo, runner, target, err := c.load()
	if err != nil {
		return err
	}

	srv := &console.Server{
		Root:      repo.Root,
		Runner:    runner,
		AuditPath: auditPath(repo),
	}

	if !isLoopback(*addr) {
		fmt.Printf("WARNING: %s is not a loopback address. The console has no\n"+
			"authentication — anyone who can reach it can read fleet state and\n"+
			"deploy history. Prefer the default and tunnel in:\n"+
			"  ssh -L 8600:127.0.0.1:8600 %s\n\n", *addr, target.Address)
	}

	fmt.Printf("alert-platform console (read-only)\n")
	fmt.Printf("  fleet   %s (%d services)\n", repo.Fleet.Metadata.Name, len(repo.Services))
	fmt.Printf("  target  %s\n", runner.Describe())
	fmt.Printf("  audit   %s\n", srv.AuditPath)
	fmt.Printf("  url     http://%s/\n", *addr)
	return http.ListenAndServe(*addr, srv.Handler()) //nolint:gosec // loopback console; see warning above
}

func isLoopback(addr string) bool {
	host, _, err := net.SplitHostPort(addr)
	if err != nil {
		host = addr
	}
	if host == "" || strings.EqualFold(host, "localhost") {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}
