//go:build !windows

package main

type statsProbe struct{}

func openStatsProbe() *statsProbe { return &statsProbe{} }

// Only the Windows build ships: elsewhere there is nothing to report.
func (p *statsProbe) sample(s *statsSample) bool { return false }

func (p *statsProbe) close() {}
