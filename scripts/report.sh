#!/usr/bin/env bash
# report.sh - print a Markdown summary of an LLM machine with AMD and/or Intel GPUs: board, BIOS, CPU, memory,
# GPUs (with the real PCIe link to the CPU), storage and software. Run as a normal user; run with sudo to add the DIMM
# details (dmidecode needs root). Prints no serial numbers, UUIDs, MAC addresses or host names.
#
#   bash scripts/report.sh > my-rig.md
#   LLAMA_BIN=~/locai/llama/bin bash scripts/report.sh     # also report the llama.cpp build
#   LLAMA_SYCL_BIN=~/locai/llama/sycl bash scripts/report.sh  # and the SYCL build (Intel GPUs)
set -u
rd() { cat "$1" 2>/dev/null | tr -d '\n'; }
dmi() { rd "/sys/class/dmi/id/$1"; }
# summarise `dmidecode -t memory` (stdin): sizes, part numbers, speeds and slots, never serial numbers
dimms() {
    awk -F': ' '
        /^Memory Device/ {d=1; size=""; loc=""; type=""; speed=""; cfg=""; man=""; part=""; rank=""; next}
        d && /^\tSize:/ {size=$2} d && /^\tLocator:/ {loc=$2} d && /^\tType:/ {type=$2}
        d && /^\tSpeed:/ {speed=$2} d && /^\tConfigured Memory Speed:/ {cfg=$2}
        d && /^\tManufacturer:/ {man=$2} d && /^\tPart Number:/ {gsub(/ +$/,"",$2); part=$2} d && /^\tRank:/ {rank=$2}
        d && /^$/ {d=0; if (size !~ /No Module/ && size != "") { n++; key=size" "type" "man" "part" rank "rank" @ "cfg; c[key]++; slots=slots" "loc } }
        END {for (k in c) printf "- DIMMs: %d x %s\n", c[k], k; printf "- Populated slots (%d):%s\n", n, slots}'
}

echo "## System"
. /etc/os-release 2>/dev/null
echo "- OS: ${PRETTY_NAME:-unknown}"
echo "- Kernel: $(uname -r)"
echo "- Board: $(dmi board_vendor) $(dmi board_name) (rev $(dmi board_version))"
echo "- BIOS: $(dmi bios_vendor) $(dmi bios_version) ($(dmi bios_date))"

echo
echo "## CPU and memory"
echo "- CPU: $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2- | sed 's/^ *//')"
echo "- Cores / threads: $(lscpu -p=CORE 2>/dev/null | grep -v '^#' | sort -u | wc -l) / $(nproc --all)"
echo "- NUMA nodes: $(lscpu 2>/dev/null | awk -F: '/^NUMA node\(s\)/{gsub(/ /,"",$2);print $2}')"
echo "- RAM: $(free -g | awk '/^Mem:/{print $2}') GiB"
if [ "$(id -u)" = 0 ] && command -v dmidecode >/dev/null; then
    dmidecode -t memory | dimms
else
    echo "- DIMMs: run with sudo to list them"
fi

echo
echo "## GPUs"
echo
echo "| GPU | Driver | PCI | VRAM | VBIOS | Power limit | Link to CPU |"
echo "|---|---|---|---|---|---|---|"
intel=0
for dev in /sys/bus/pci/devices/*; do
    case "$(rd "$dev/class")" in 0x03*) ;; *) continue ;; esac   # display controllers only
    vendor=$(rd "$dev/vendor"); bdf=$(basename "$dev")
    drv=$(basename "$(readlink -f "$dev/driver" 2>/dev/null)")
    case "$vendor" in
        0x1002)   # AMD: amdgpu reports VRAM in sysfs
            [ -e "$dev/mem_info_vram_total" ] || continue
            vram="$(( ($(rd "$dev/mem_info_vram_total") + 536870912) / 1073741824 )) GB" ;;
        0x8086)   # Intel: the xe driver has no VRAM file; ask the render node (needs the render group)
            [ "$drv" = xe ] || continue   # integrated i915 graphics: not an LLM card
            intel=1
            node=$(ls "$dev/drm" 2>/dev/null | grep -m1 '^renderD')
            v=$(python3 -I - "/dev/dri/$node" 2>/dev/null <<'EOF'
import ctypes, fcntl, os, struct, sys
Q = 0xC0000000 | (40 << 16) | (0x64 << 8) | 0x40      # DRM_IOCTL_XE_DEVICE_QUERY
fd = os.open(sys.argv[1], os.O_RDWR)
r = bytearray(struct.pack("<QIIQQQ", 0, 1, 0, 0, 0, 0))  # query 1 = memory regions; first ask the size
fcntl.ioctl(fd, Q, r); size = struct.unpack_from("<I", r, 12)[0]
b = ctypes.create_string_buffer(size)
fcntl.ioctl(fd, Q, bytearray(struct.pack("<QIIQQQ", 0, 1, size, ctypes.addressof(b), 0, 0)))
raw = b.raw
print(sum(struct.unpack_from("<HHIQQ", raw, 8 + i * 88)[3] for i in range(struct.unpack_from("<I", raw, 0)[0])
          if struct.unpack_from("<H", raw, 8 + i * 88)[0] == 1))   # class 1 = VRAM
EOF
)
            vram=${v:+"$(( (v + 536870912) / 1073741824 )) GB"} ;;
        *) continue ;;
    esac
    name=$(lspci -s "$bdf" 2>/dev/null | sed 's/^[^:]*: *[^:]*: *//; s/^Advanced Micro Devices, Inc. \[AMD\/ATI\] *//; s/^Intel Corporation *//')
    cap=$(rd "$(ls "$dev"/hwmon/hwmon*/power1_cap 2>/dev/null | head -n1)")
    capmax=$(rd "$(ls "$dev"/hwmon/hwmon*/power1_cap_max 2>/dev/null | head -n1)")
    # walk up to the CPU root port: the GPU's own link is often its internal bridge, not the real slot link
    p=$(readlink -f "$dev"); link=""
    while [ -e "$p/current_link_speed" ]; do link="$(rd "$p/current_link_speed" | sed 's/ PCIe//') x$(rd "$p/current_link_width") (port $(basename "$p"))"; p=$(dirname "$p"); done
    echo "| ${name:-GPU} | ${drv:-none} | \`$bdf\` | ${vram:-?} | $(rd "$dev/vbios_version") | ${cap:+$((cap/1000000)) W}${capmax:+ (max $((capmax/1000000)) W)} | $link |"
done
echo
echo "Device order: ROCm (\`HIP_VISIBLE_DEVICES\`) and Level Zero (\`ONEAPI_DEVICE_SELECTOR\`) number the cards their own"
echo "way, which may differ from the PCI order above; check with a short load."

echo
echo "## Storage"
for n in /sys/class/nvme/nvme*; do
    [ -e "$n/model" ] || continue
    echo "- $(rd "$n/model" | sed 's/ *$//') (firmware $(rd "$n/firmware_rev" | sed 's/ *$//')): $(rd "$n/device/current_link_speed" | sed 's/ PCIe//') x$(rd "$n/device/current_link_width")"
done

echo
echo "## Software"
if command -v amd-smi >/dev/null; then
    echo "- $(amd-smi version 2>/dev/null | head -n1 | cut -d'|' -f1-3 | sed 's/ *$//')"
fi
[ -r /opt/rocm/.info/version ] && echo "- /opt/rocm: $(rd /opt/rocm/.info/version)"
v=$(modinfo -F version amdgpu 2>/dev/null); echo "- amdgpu: ${v:-in-tree driver} (kernel $(uname -r))"
if [ "$intel" = 1 ]; then
    echo "- xe: in-tree driver (kernel $(uname -r))"
    v=$(dpkg-query -W -f '${Version}' intel-oneapi-runtime-dpcpp-sycl-core 2>/dev/null); [ -n "$v" ] && echo "- oneAPI SYCL runtime: $v"
    [ -e /opt/intel/oneapi/umf/latest/lib/libumf.so.1 ] && echo "- oneAPI UMF: installed" || echo "- oneAPI UMF: not found (SYCL falls back to OpenCL)"
fi
if [ -n "${LLAMA_BIN:-}" ] && [ -x "$LLAMA_BIN/llama-server" ]; then
    echo "- llama.cpp: $(LD_LIBRARY_PATH="$LLAMA_BIN" "$LLAMA_BIN/llama-server" --version 2>&1 | grep -m1 -i version)"
fi
if [ -n "${LLAMA_SYCL_BIN:-}" ] && [ -x "$LLAMA_SYCL_BIN/llama-server" ]; then
    echo "- llama.cpp SYCL: $(LD_LIBRARY_PATH="/opt/intel/oneapi/umf/latest/lib:$LLAMA_SYCL_BIN" "$LLAMA_SYCL_BIN/llama-server" --version 2>&1 | grep -m1 -i version)"
fi
