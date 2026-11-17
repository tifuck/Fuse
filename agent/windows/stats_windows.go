//go:build windows

package main

import (
	"fmt"
	"os"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
)

var (
	procGetSystemTimes       = modKernel32.NewProc("GetSystemTimes")
	procGlobalMemoryStatusEx = modKernel32.NewProc("GlobalMemoryStatusEx")
)

// IOCTL_DISK_PERFORMANCE is FILE_ANY_ACCESS: a handle opened with no
// access rights reads the counters, so this needs neither admin nor a
// read handle on the disk.
const ioctlDiskPerformance = 0x70020

// DISK_PERFORMANCE from winioctl.h; times are in 100 ns units.
type diskPerformance struct {
	BytesRead, BytesWritten, ReadTime, WriteTime, IdleTime int64
	ReadCount, WriteCount, QueueDepth, SplitCount          uint32
	QueryTime                                              int64
	StorageDeviceNumber                                    uint32
	StorageManagerName                                     [8]uint16
}

// MEMORYSTATUSEX from sysinfoapi.h.
type memoryStatusEx struct {
	Length, MemoryLoad                     uint32
	TotalPhys, AvailPhys                   uint64
	TotalPageFile, AvailPageFile           uint64
	TotalVirtual, AvailVirtual, AvailExtra uint64
}

// MIB_IF_ROW2.InterfaceAndOperStatusFlags bits.
const (
	ifHardwareInterface = 0x01
	ifFilterInterface   = 0x02
)

type statsProbe struct {
	disks []windows.Handle
	vol   *uint16
}

func openStatsProbe() *statsProbe {
	p := &statsProbe{}
	for i := range 32 {
		name, _ := windows.UTF16PtrFromString(fmt.Sprintf(`\\.\PhysicalDrive%d`, i))
		h, err := windows.CreateFile(name, 0, windows.FILE_SHARE_READ|windows.FILE_SHARE_WRITE, nil, windows.OPEN_EXISTING, 0, 0)
		if err == nil {
			p.disks = append(p.disks, h)
		}
	}
	drive := os.Getenv("SystemDrive")
	if drive == "" {
		drive = "C:"
	}
	p.vol, _ = windows.UTF16PtrFromString(drive + `\`)
	return p
}

func ft64(t windows.Filetime) uint64 { return uint64(t.HighDateTime)<<32 | uint64(t.LowDateTime) }

func (p *statsProbe) sample(s *statsSample) bool {
	s.at = time.Now()
	var idle, kernel, user windows.Filetime
	if r, _, _ := procGetSystemTimes.Call(uintptr(unsafe.Pointer(&idle)), uintptr(unsafe.Pointer(&kernel)), uintptr(unsafe.Pointer(&user))); r != 0 {
		// Kernel time includes idle time.
		s.cpuTotal = ft64(kernel) + ft64(user)
		s.cpuBusy = s.cpuTotal - ft64(idle)
	}
	m := memoryStatusEx{Length: uint32(unsafe.Sizeof(memoryStatusEx{}))}
	if r, _, _ := procGlobalMemoryStatusEx.Call(uintptr(unsafe.Pointer(&m))); r != 0 {
		s.memTotal, s.memUsed = m.TotalPhys, m.TotalPhys-m.AvailPhys
	}
	var table *windows.MibIfTable2
	if windows.GetIfTable2Ex(windows.MibIfTableNormal, &table) == nil {
		// Physical adapters that are up: virtual switches, filters and
		// tunnels would count the same traffic again.
		for _, row := range unsafe.Slice(&table.Table[0], table.NumEntries) {
			if row.InterfaceAndOperStatusFlags&ifHardwareInterface != 0 &&
				row.InterfaceAndOperStatusFlags&ifFilterInterface == 0 &&
				row.OperStatus == windows.IfOperStatusUp {
				s.netRx += row.InOctets
				s.netTx += row.OutOctets
			}
		}
		windows.FreeMibTable(unsafe.Pointer(table))
	}
	s.diskBusy = make([]uint64, len(p.disks))
	s.diskTime = make([]uint64, len(p.disks))
	for i, h := range p.disks {
		var d diskPerformance
		var n uint32
		if windows.DeviceIoControl(h, ioctlDiskPerformance, nil, 0, (*byte)(unsafe.Pointer(&d)), uint32(unsafe.Sizeof(d)), &n, nil) != nil {
			continue
		}
		s.diskR += uint64(d.BytesRead)
		s.diskW += uint64(d.BytesWritten)
		s.diskTime[i] = uint64(d.QueryTime)
		s.diskBusy[i] = statsDelta(uint64(d.IdleTime), uint64(d.QueryTime))
	}
	var free, total, totalFree uint64
	if p.vol != nil && windows.GetDiskFreeSpaceEx(p.vol, &free, &total, &totalFree) == nil {
		s.volTotal, s.volUsed = total, total-totalFree
	}
	return true
}

func (p *statsProbe) close() {
	for _, h := range p.disks {
		windows.CloseHandle(h)
	}
	p.disks = nil
}
