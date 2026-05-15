"""
mesh/discovery.py

Universal Soul AI — Mesh Device Discovery

Discovers devices on the local network that are running Universal Soul AI.
Uses mDNS (Zeroconf) for zero-configuration device discovery.

Each device broadcasts:
    - Device ID and role (mobile / laptop / desktop / server)
    - Available model tiers and their VRAM requirements
    - Current load (CPU %, VRAM free GB)
    - Universal Soul version

HRM reads the live mesh state to assign branches to the best available device.
"""

from __future__ import annotations

import asyncio
import json
import logging
import platform
import socket
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Callable

logger = logging.getLogger(__name__)

# mDNS service type for Universal Soul
SERVICE_TYPE = "_universal-soul._tcp.local."
SERVICE_PORT = 7437   # US-AI = 7437 — chosen as a memorable port

try:
    from zeroconf import ServiceBrowser, ServiceInfo, Zeroconf
    from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf
    ZEROCONF_AVAILABLE = True
except ImportError:
    ZEROCONF_AVAILABLE = False
    logger.warning(
        "zeroconf not installed — mesh discovery disabled. "
        "Install with: pip install zeroconf"
    )


@dataclass
class MeshDevice:
    """Represents a Universal Soul node on the mesh network."""
    device_id: str
    hostname: str
    ip_address: str
    port: int
    role: str                          # mobile / laptop / desktop / server
    model_tiers: List[str]             # e.g. ["1.5b", "7b", "14b"]
    vram_total_gb: float
    vram_free_gb: float
    cpu_load_pct: float
    universal_soul_version: str
    last_seen: float = field(default_factory=time.time)

    @property
    def is_stale(self) -> bool:
        """Device is considered stale if not seen in 30 seconds."""
        return time.time() - self.last_seen > 30.0

    @property
    def is_available(self) -> bool:
        """Device is available if not stale and not overloaded."""
        return not self.is_stale and self.cpu_load_pct < 90.0

    @property
    def capacity_score(self) -> float:
        """0.0–1.0 score of available compute capacity."""
        vram_score = min(1.0, self.vram_free_gb / max(self.vram_total_gb, 1))
        cpu_score = 1.0 - (self.cpu_load_pct / 100.0)
        return (vram_score * 0.6) + (cpu_score * 0.4)

    def can_run_tier(self, tier: str) -> bool:
        """Check if device can handle a given model tier."""
        tier_vram = {"1.5b": 2.0, "7b": 6.0, "14b": 10.0, "32b": 22.0}
        required = tier_vram.get(tier, 6.0)
        return tier in self.model_tiers and self.vram_free_gb >= required

    def to_dict(self) -> Dict:
        return asdict(self)


class MeshDiscovery:
    """
    Manages device discovery and mesh state.

    Usage:
        discovery = MeshDiscovery(role="desktop", model_tiers=["7b", "14b"])
        await discovery.start()
        
        # Get best device for a tier
        device = discovery.best_device_for_tier("14b")
        
        # Get full mesh state (for HRM)
        state = discovery.mesh_state
        
        await discovery.stop()
    """

    def __init__(
        self,
        role: str = "desktop",
        model_tiers: Optional[List[str]] = None,
        vram_total_gb: float = 24.0,
        on_device_added: Optional[Callable[[MeshDevice], None]] = None,
        on_device_removed: Optional[Callable[[str], None]] = None,
    ):
        self.device_id = str(uuid.uuid4())[:12]
        self.role = role
        self.model_tiers = model_tiers or ["7b"]
        self.vram_total_gb = vram_total_gb
        self.on_device_added = on_device_added
        self.on_device_removed = on_device_removed

        self._devices: Dict[str, MeshDevice] = {}
        self._zeroconf: Optional[object] = None
        self._service_info: Optional[object] = None
        self._running = False

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def start(self) -> None:
        """Start discovery and broadcasting."""
        if not ZEROCONF_AVAILABLE:
            logger.warning("Mesh discovery unavailable — zeroconf not installed")
            return

        self._running = True
        self._zeroconf = AsyncZeroconf()

        # Register this device on the network
        await self._register_self()

        # Start browsing for other devices
        self._browser = AsyncServiceBrowser(
            self._zeroconf.zeroconf,
            SERVICE_TYPE,
            handlers=[self._on_service_state_change],
        )

        # Start stale device cleanup loop
        asyncio.create_task(self._cleanup_stale_devices())

        logger.info(
            f"Mesh discovery started — device_id={self.device_id} "
            f"role={self.role} tiers={self.model_tiers}"
        )

    async def stop(self) -> None:
        """Stop discovery and deregister from network."""
        self._running = False
        if self._zeroconf:
            if self._service_info:
                await self._zeroconf.async_unregister_service(self._service_info)
            await self._zeroconf.async_close()
        logger.info("Mesh discovery stopped")

    # -------------------------------------------------------------------------
    # Queries
    # -------------------------------------------------------------------------

    @property
    def mesh_state(self) -> Dict[str, Dict]:
        """Current mesh state — used by HRM for branch assignment."""
        return {
            device_id: device.to_dict()
            for device_id, device in self._devices.items()
            if device.is_available
        }

    def best_device_for_tier(self, tier: str) -> Optional[MeshDevice]:
        """
        Find the best available device for a given model tier.
        Scores by capacity (VRAM + CPU) among devices that can run the tier.
        """
        candidates = [
            device for device in self._devices.values()
            if device.is_available and device.can_run_tier(tier)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda d: d.capacity_score)

    def all_available_devices(self) -> List[MeshDevice]:
        """All currently available devices sorted by capacity."""
        available = [d for d in self._devices.values() if d.is_available]
        return sorted(available, key=lambda d: d.capacity_score, reverse=True)

    def device_count(self) -> int:
        return len([d for d in self._devices.values() if d.is_available])

    # -------------------------------------------------------------------------
    # Internal
    # -------------------------------------------------------------------------

    async def _register_self(self) -> None:
        """Broadcast this device's capabilities on the local network."""
        hostname = socket.gethostname()
        local_ip = self._get_local_ip()

        properties = {
            b"device_id": self.device_id.encode(),
            b"role": self.role.encode(),
            b"model_tiers": json.dumps(self.model_tiers).encode(),
            b"vram_total_gb": str(self.vram_total_gb).encode(),
            b"vram_free_gb": str(self._get_vram_free()).encode(),
            b"cpu_load_pct": str(self._get_cpu_load()).encode(),
            b"version": b"1.0.0",
            b"platform": platform.system().encode(),
        }

        self._service_info = ServiceInfo(
            SERVICE_TYPE,
            f"{self.device_id}.{SERVICE_TYPE}",
            addresses=[socket.inet_aton(local_ip)],
            port=SERVICE_PORT,
            properties=properties,
            server=f"{hostname}.local.",
        )

        await self._zeroconf.async_register_service(self._service_info)
        logger.debug(f"Registered on mesh: {local_ip}:{SERVICE_PORT}")

    def _on_service_state_change(self, zeroconf, service_type, name, state_change) -> None:
        """Handle device appearing/disappearing on the mesh."""
        from zeroconf import ServiceStateChange

        if state_change == ServiceStateChange.Added:
            asyncio.create_task(self._handle_device_added(zeroconf, service_type, name))
        elif state_change == ServiceStateChange.Removed:
            self._handle_device_removed(name)

    async def _handle_device_added(self, zeroconf, service_type: str, name: str) -> None:
        """Parse and register a newly discovered device."""
        info = ServiceInfo(service_type, name)
        if not await asyncio.get_event_loop().run_in_executor(
            None, info.request, zeroconf, 3000
        ):
            return

        props = info.decoded_properties
        device_id = props.get("device_id", name[:12])

        # Don't add ourselves
        if device_id == self.device_id:
            return

        ip = socket.inet_ntoa(info.addresses[0]) if info.addresses else "unknown"

        device = MeshDevice(
            device_id=device_id,
            hostname=info.server or "unknown",
            ip_address=ip,
            port=info.port,
            role=props.get("role", "desktop"),
            model_tiers=json.loads(props.get("model_tiers", '["7b"]')),
            vram_total_gb=float(props.get("vram_total_gb", 8)),
            vram_free_gb=float(props.get("vram_free_gb", 4)),
            cpu_load_pct=float(props.get("cpu_load_pct", 0)),
            universal_soul_version=props.get("version", "unknown"),
        )

        self._devices[device_id] = device
        logger.info(
            f"Device joined mesh: {device_id} role={device.role} "
            f"tiers={device.model_tiers} ip={ip}"
        )

        if self.on_device_added:
            self.on_device_added(device)

    def _handle_device_removed(self, name: str) -> None:
        """Remove a device that left the mesh."""
        # Extract device_id from service name
        device_id = name.split(".")[0]
        if device_id in self._devices:
            del self._devices[device_id]
            logger.info(f"Device left mesh: {device_id}")
            if self.on_device_removed:
                self.on_device_removed(device_id)

    async def _cleanup_stale_devices(self) -> None:
        """Periodically remove devices that haven't been seen recently."""
        while self._running:
            stale = [did for did, d in self._devices.items() if d.is_stale]
            for did in stale:
                del self._devices[did]
                logger.debug(f"Removed stale device: {did}")
            await asyncio.sleep(15.0)

    @staticmethod
    def _get_local_ip() -> str:
        """Get this machine's local IP address."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    @staticmethod
    def _get_vram_free() -> float:
        """Get free VRAM in GB. Returns 0 if no GPU."""
        try:
            import subprocess
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=3
            )
            if result.returncode == 0:
                mb_free = float(result.stdout.strip().split("\n")[0])
                return round(mb_free / 1024, 1)
        except Exception:
            pass
        return 0.0

    @staticmethod
    def _get_cpu_load() -> float:
        """Get current CPU load percentage."""
        try:
            import psutil
            return psutil.cpu_percent(interval=0.1)
        except Exception:
            return 0.0
