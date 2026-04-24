"""
Couche transport TCP pour le protocole Fanuc RMI.

Gère la connexion socket et le framing des paquets JSON terminés par \\r\\n.
C'est le **seul** module qui touche directement au socket.

Protocole RMI (doc B-84184EN/03 §2.2) :
  - Chaque paquet est une chaîne JSON ASCII terminée par \\r\\n
  - Un seul recv() TCP peut contenir plusieurs paquets (concaténation TCP)
  - Un paquet peut être fragmenté sur plusieurs recv() (paquets partiels)
  → Le framing sur \\r\\n est donc indispensable.
"""

import json
import socket
import threading
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class RmiTransportError(Exception):
    """Erreur de la couche transport RMI."""


class ConnectionLostError(RmiTransportError):
    """Le pair a fermé la connexion."""


class RmiTransport:
    """Transport TCP avec framing \\r\\n pour le protocole Fanuc RMI.

    Thread-safety :
      - send() est protégé par un Lock (plusieurs threads peuvent envoyer)
      - recv_packet() est appelé par un seul thread (RecvThread dans le Dispatcher)
    """

    def __init__(self) -> None:
        self._sock: Optional[socket.socket] = None
        self._send_lock = threading.Lock()
        self._recv_buffer = b""
        self._connected = False

    # ------------------------------------------------------------------
    # Connexion / déconnexion
    # ------------------------------------------------------------------

    def connect(self, ip: str, port: int, timeout: float = 5.0) -> None:
        """Ouvre une connexion TCP vers le contrôleur.

        Args:
            ip: Adresse IP du robot.
            port: Port TCP (16001 pour FRC_Connect, puis PortNumber retourné).
            timeout: Timeout de connexion en secondes.
        """
        if self._connected:
            self.close()

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect((ip, port))
        except Exception:
            sock.close()
            raise

        self._sock = sock
        self._recv_buffer = b""
        self._connected = True
        logger.info("Transport connecté à %s:%d", ip, port)

    def close(self) -> None:
        """Ferme proprement la connexion TCP."""
        if self._sock:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self._connected = False
        self._recv_buffer = b""
        logger.info("Transport fermé")

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Envoi (thread-safe, multi-appelant)
    # ------------------------------------------------------------------

    def send(self, packet: Dict[str, Any]) -> None:
        """Envoie un dict Python sous forme de JSON ASCII terminé par \\r\\n.

        Args:
            packet: Dictionnaire à sérialiser en JSON.

        Raises:
            RmiTransportError: Si non connecté ou erreur d'envoi.
        """
        if not self._connected or not self._sock:
            raise RmiTransportError("Transport non connecté")

        raw = json.dumps(packet, separators=(",", ":")) + "\r\n"
        with self._send_lock:
            try:
                self._sock.sendall(raw.encode("ascii"))
            except OSError as exc:
                self._connected = False
                raise ConnectionLostError(f"Erreur envoi: {exc}") from exc

    # ------------------------------------------------------------------
    # Réception (mono-thread — appelé uniquement par RecvThread)
    # ------------------------------------------------------------------

    def recv_packet(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Lit UN paquet JSON complet depuis le buffer TCP.

        Gère :
          - Les paquets partiels (accumulation dans self._recv_buffer)
          - Les paquets multiples dans un seul recv() TCP
          - Le délimiteur \\r\\n conforme à la spec RMI

        Args:
            timeout: Timeout optionnel pour cette lecture (secondes).
                     None = utilise le timeout du socket.

        Returns:
            Dict parsé du paquet JSON.

        Raises:
            ConnectionLostError: Si le pair ferme la connexion.
            RmiTransportError: Si non connecté.
        """
        if not self._connected or not self._sock:
            raise RmiTransportError("Transport non connecté")

        prev_timeout = self._sock.gettimeout()
        if timeout is not None:
            self._sock.settimeout(timeout)

        try:
            # Accumuler des données jusqu'à trouver \r\n
            while b"\r\n" not in self._recv_buffer:
                try:
                    chunk = self._sock.recv(4096)
                except socket.timeout:
                    raise  # Remonter le timeout tel quel
                if not chunk:
                    self._connected = False
                    raise ConnectionLostError("Connexion fermée par le contrôleur")
                self._recv_buffer += chunk

            # Découper sur le premier \r\n
            line, self._recv_buffer = self._recv_buffer.split(b"\r\n", 1)
            return json.loads(line.decode("ascii"))

        finally:
            if timeout is not None:
                try:
                    self._sock.settimeout(prev_timeout)
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Utilitaire : send + recv synchrone (utilisé seulement pour FRC_Connect
    # sur le port 16001, avant que le Dispatcher soit démarré)
    # ------------------------------------------------------------------

    def send_recv(self, packet: Dict[str, Any], timeout: float = 10.0) -> Dict[str, Any]:
        """Envoie un paquet et attend la réponse (mode synchrone simple).

        ⚠️ À utiliser UNIQUEMENT avant le démarrage du RecvThread (phase FRC_Connect).
        Après, toute réception passe par le Dispatcher.
        """
        self.send(packet)
        return self.recv_packet(timeout=timeout)
