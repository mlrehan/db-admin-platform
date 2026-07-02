// Global top progress bar: a thin animated bar at the very top of the window that appears
// whenever one or more HTTP requests are in flight, so the user always knows the app is
// working during any slower operation. Driven by the `http:activity` event the HttpClient
// dispatches. Also performs a one-shot best-effort detection of the browser's LAN IP (WebRTC)
// which is then sent on requests for the audit trail (admins only).

import { HttpClient } from "../services/http.js";

export function initLoadingBar() {
  const bar = document.createElement("div");
  bar.id = "global-loading";
  bar.setAttribute("aria-hidden", "true");
  document.body.appendChild(bar);

  let hideTimer = null;
  window.addEventListener("http:activity", (e) => {
    const active = e.detail?.active || 0;
    if (active > 0) {
      clearTimeout(hideTimer);
      bar.classList.add("on");
    } else {
      // Small delay so back-to-back requests don't flicker the bar off then on again.
      hideTimer = setTimeout(() => bar.classList.remove("on"), 200);
    }
  });

  detectPrivateIp();
}

// Best-effort local-IP discovery via a WebRTC ICE candidate. Modern browsers may return an
// mDNS ".local" hostname instead of a real 192.168/10.x address; in that case we simply skip
// it (the backend falls back to the socket peer). Never throws.
function detectPrivateIp() {
  try {
    if (typeof RTCPeerConnection === "undefined") return;
    const pc = new RTCPeerConnection({ iceServers: [] });
    pc.createDataChannel("");
    pc.onicecandidate = (evt) => {
      const cand = evt?.candidate?.candidate;
      if (!cand) {
        pc.close();
        return;
      }
      const m = cand.match(/(\d{1,3}(?:\.\d{1,3}){3})/);
      const ip = m && m[1];
      // Keep only private/RFC1918-looking addresses; ignore mDNS/.local and public reflexive.
      if (ip && /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|169\.254\.)/.test(ip)) {
        HttpClient._privateIp = ip;
        pc.close();
      }
    };
    pc.createOffer().then((o) => pc.setLocalDescription(o)).catch(() => pc.close());
  } catch {
    /* WebRTC unavailable or blocked — private IP stays unknown (server falls back to peer). */
  }
}
