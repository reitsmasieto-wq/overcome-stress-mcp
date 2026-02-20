"""
Overcome Stress — L402 Skill Server
====================================
Serves AI-agent skill blocks behind Lightning Network paywalls.
Uses LNbits as payment backend for invoice creation and verification.

L402 Protocol Flow:
1. Agent requests GET /api/skills/{skill_id}
2. Server returns HTTP 402 + WWW-Authenticate header with Lightning invoice
3. Agent pays invoice, receives preimage
4. Agent retries with Authorization: L402 {macaroon}:{preimage}
5. Server verifies payment and returns skill content

Author: Sieto Reitsma / Ergotherapiepraktijk Kollum
License: All Rights Reserved
"""

import os
import json
import time
import hashlib
import hmac
import base64
import secrets
from pathlib import Path
from functools import wraps

from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import requests
from skill_parser import parse_skill_content
from trajectory_parser import load_trajectory, load_all_trajectories

app = Flask(__name__)
CORS(app)

# =============================================================================
# Configuration
# =============================================================================

LNBITS_URL = os.environ.get("LNBITS_URL", "http://lnbits:5000")
LNBITS_API_KEY = os.environ.get("LNBITS_API_KEY", "")  # Invoice/read key
LNBITS_ADMIN_KEY = os.environ.get("LNBITS_ADMIN_KEY", "")  # Admin key for creating invoices
SKILLS_DIR = os.environ.get("SKILLS_DIR", "/app/skills")
SERVER_SECRET = os.environ.get("SERVER_SECRET", secrets.token_hex(32))
PORT = int(os.environ.get("PORT", 8402))
MOCK_MODE = os.environ.get("MOCK_MODE", "false").lower() == "true"  # For testing without LNbits

# =============================================================================
# Skill Registry — Pricing & Metadata
# =============================================================================

SKILL_REGISTRY = {
    # Knowledge blocks — 50 sats
    "K01": {"file": "K01_what_is_stress.md", "price": 50, "type": "knowledge", "title": "What is Stress?"},
    "K02": {"file": "K02_autonomic_nervous_system.md", "price": 50, "type": "knowledge", "title": "The Autonomic Nervous System"},
    "K03": {"file": "K03_overstimulated_brain.md", "price": 50, "type": "knowledge", "title": "The Overstimulated Brain"},
    "K04": {"file": "K04_authenticity_self_image.md", "price": 50, "type": "knowledge", "title": "Authenticity and Self-Image"},
    "K05": {"file": "K05_body_awareness.md", "price": 50, "type": "knowledge", "title": "Body Awareness"},
    "K06": {"file": "K06_sleep_and_recovery.md", "price": 50, "type": "knowledge", "title": "Sleep and Recovery"},
    "K07": {"file": "K07_nutrition_and_stress.md", "price": 50, "type": "knowledge", "title": "Nutrition and Stress"},
    "K08": {"file": "K08_gratitude_neuroplasticity.md", "price": 50, "type": "knowledge", "title": "Gratitude as Neuroplasticity"},
    
    # Intervention blocks — 75 sats
    "I01": {"file": "I01_4_7_8_breathing.md", "price": 75, "type": "intervention", "title": "4-7-8 Breathing Technique"},
    "I02": {"file": "I02_activity_monitor.md", "price": 75, "type": "intervention", "title": "Activity Monitor"},
    "I03": {"file": "I03_body_scan_protocol.md", "price": 75, "type": "intervention", "title": "Body Scan Protocol"},
    "I04": {"file": "I04_grounding_techniques.md", "price": 75, "type": "intervention", "title": "Grounding Techniques"},
    "I05": {"file": "I05_sleep_hygiene_protocol.md", "price": 75, "type": "intervention", "title": "Sleep Hygiene Protocol"},
    "I06": {"file": "I06_movement_exercise.md", "price": 75, "type": "intervention", "title": "Movement and Exercise"},
    "I07": {"file": "I07_gratitude_practice.md", "price": 75, "type": "intervention", "title": "Gratitude Practice"},
    
    # Proprietary blocks — 100 sats
    "I08": {"file": "I08_vergeetmuts_technique.md", "price": 100, "type": "proprietary", "title": "Forgive and Forget Hood (VergeetMuts)"},
    "C01": {"file": "C01_co_regulation_protocol.md", "price": 100, "type": "proprietary", "title": "Co-Regulation Protocol (Corpus Systemics®)"},
}

# Trajectory pricing
TRAJECTORY_REGISTRY = {
    "T01": {"price": 150, "title": "Post-Concussion Syndrome — 12 Week Recovery Path", "type": "trajectory"},
    "T02": {"price": 150, "title": "Post-COVID — 12 Week Recovery Path", "type": "trajectory"},
    "T03": {"price": 150, "title": "Burnout — 16 Week Recovery Path", "type": "trajectory"},
    "T04": {"price": 150, "title": "Chronic Stress / Prevention — 8 Week Path", "type": "trajectory"},
}

# =============================================================================
# Payment token management (in-memory + file persistence)
# =============================================================================

# Stores: {payment_hash: {"skill_id": str, "paid": bool, "created": float, "preimage": str}}
payment_store = {}
PAYMENT_STORE_FILE = "/app/data/payments.json"

def load_payments():
    """Load payment store from disk."""
    global payment_store
    try:
        if os.path.exists(PAYMENT_STORE_FILE):
            with open(PAYMENT_STORE_FILE, "r") as f:
                payment_store = json.load(f)
    except Exception:
        payment_store = {}

def save_payments():
    """Persist payment store to disk."""
    os.makedirs(os.path.dirname(PAYMENT_STORE_FILE), exist_ok=True)
    with open(PAYMENT_STORE_FILE, "w") as f:
        json.dump(payment_store, f)

def cleanup_expired_payments():
    """Remove payments older than 24 hours."""
    now = time.time()
    expired = [h for h, p in payment_store.items() if now - p.get("created", 0) > 86400]
    for h in expired:
        del payment_store[h]
    if expired:
        save_payments()

# =============================================================================
# LNbits API integration
# =============================================================================

def create_invoice(amount_sats: int, memo: str) -> dict:
    """Create a Lightning invoice via LNbits API (or mock for testing)."""
    
    if MOCK_MODE:
        # Generate deterministic mock invoice for testing
        mock_hash = hashlib.sha256(f"{memo}{time.time()}".encode()).hexdigest()
        mock_preimage = hashlib.sha256(mock_hash.encode()).hexdigest()
        return {
            "payment_hash": mock_hash,
            "payment_request": f"lnbc{amount_sats}n1mock_{mock_hash[:20]}",
            "amount": amount_sats,
            "_mock_preimage": mock_preimage,  # Only in test mode
        }
    
    url = f"{LNBITS_URL}/api/v1/payments"
    headers = {
        "X-Api-Key": LNBITS_ADMIN_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "out": False,
        "amount": amount_sats,
        "memo": memo,
        "unit": "sat"
    }
    
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return {
            "payment_hash": data["payment_hash"],
            "payment_request": data["payment_request"],
            "amount": amount_sats
        }
    except Exception as e:
        app.logger.error(f"LNbits invoice creation failed: {e}")
        return None

def check_invoice_paid(payment_hash: str) -> bool:
    """Check if a Lightning invoice has been paid via LNbits API."""
    
    if MOCK_MODE:
        return payment_store.get(payment_hash, {}).get("paid", False)
    
    url = f"{LNBITS_URL}/api/v1/payments/{payment_hash}"
    headers = {"X-Api-Key": LNBITS_API_KEY}
    
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("paid", False)
    except Exception as e:
        app.logger.error(f"LNbits payment check failed: {e}")
        return False

# =============================================================================
# Macaroon-like token generation (simplified L402)
# =============================================================================

def create_macaroon(payment_hash: str, skill_id: str) -> str:
    """Create a simple macaroon token binding payment_hash to skill_id."""
    payload = f"{payment_hash}:{skill_id}:{int(time.time())}"
    signature = hmac.new(
        SERVER_SECRET.encode(), 
        payload.encode(), 
        hashlib.sha256
    ).hexdigest()
    token = base64.urlsafe_b64encode(f"{payload}:{signature}".encode()).decode()
    return token

def verify_l402_auth(auth_header: str) -> dict:
    """
    Verify an L402 Authorization header.
    Format: L402 {macaroon}:{preimage}
    Returns {"valid": bool, "skill_id": str} 
    """
    if not auth_header or not auth_header.startswith("L402 "):
        return {"valid": False, "error": "Missing L402 authorization"}
    
    token_part = auth_header[5:]  # Remove "L402 " prefix
    
    try:
        # Split macaroon:preimage
        parts = token_part.split(":")
        if len(parts) < 2:
            return {"valid": False, "error": "Invalid L402 format"}
        
        macaroon_b64 = parts[0]
        preimage = parts[1]
        
        # Decode macaroon
        macaroon_data = base64.urlsafe_b64decode(macaroon_b64).decode()
        mac_parts = macaroon_data.split(":")
        
        if len(mac_parts) != 4:
            return {"valid": False, "error": "Invalid macaroon structure"}
        
        payment_hash, skill_id, timestamp, signature = mac_parts
        
        # Verify signature
        payload = f"{payment_hash}:{skill_id}:{timestamp}"
        expected_sig = hmac.new(
            SERVER_SECRET.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()
        
        if not hmac.compare_digest(signature, expected_sig):
            return {"valid": False, "error": "Invalid macaroon signature"}
        
        # Verify preimage matches payment_hash
        preimage_bytes = bytes.fromhex(preimage)
        computed_hash = hashlib.sha256(preimage_bytes).hexdigest()
        
        if computed_hash != payment_hash:
            # Fallback: check if payment is confirmed via LNbits
            if not check_invoice_paid(payment_hash):
                return {"valid": False, "error": "Payment not verified"}
        
        # Check expiry (24 hours)
        if time.time() - int(timestamp) > 86400:
            return {"valid": False, "error": "Token expired"}
        
        return {"valid": True, "skill_id": skill_id, "payment_hash": payment_hash}
        
    except Exception as e:
        return {"valid": False, "error": f"Verification failed: {str(e)}"}

# =============================================================================
# Skill content loader
# =============================================================================

def load_skill_content(skill_id: str) -> str:
    """Load skill content from markdown file."""
    if skill_id not in SKILL_REGISTRY:
        return None
    
    filepath = Path(SKILLS_DIR) / SKILL_REGISTRY[skill_id]["file"]
    try:
        return filepath.read_text(encoding="utf-8")
    except FileNotFoundError:
        app.logger.error(f"Skill file not found: {filepath}")
        return None

# =============================================================================
# API Routes
# =============================================================================

@app.route("/", methods=["GET"])
def index():
    """Server info and API documentation."""
    return jsonify({
        "name": "Overcome Stress — AI-Agent Skill Server",
        "version": "1.0.0",
        "author": "Sieto Reitsma, BSc OT | Official ReAttach Trainer",
        "protocol": "L402",
        "payment": "Lightning Network (sats)",
        "endpoints": {
            "GET /api/catalog": "Browse available skills (free)",
            "GET /api/skills/{id}": "Request skill content (L402 paywall)",
            "GET /api/skills/{id}/preview": "Free preview of skill (free)",
            "GET /api/trajectories/{id}": "Request trajectory routing (L402 paywall)",
            "GET /api/payment/{hash}/status": "Check payment status",
        },
        "total_skills": len(SKILL_REGISTRY),
        "total_trajectories": len(TRAJECTORY_REGISTRY),
    })


@app.route("/api/catalog", methods=["GET"])
def catalog():
    """
    Free endpoint: returns the full skill catalog with metadata, pricing,
    and routing info. This is what AI agents use to discover available skills.
    """
    skills = []
    for skill_id, meta in SKILL_REGISTRY.items():
        skills.append({
            "id": skill_id,
            "title": meta["title"],
            "type": meta["type"],
            "price_sats": meta["price"],
            "endpoint": f"/api/skills/{skill_id}",
            "preview": f"/api/skills/{skill_id}/preview",
        })
    
    trajectories = []
    for traj_id, meta in TRAJECTORY_REGISTRY.items():
        trajectories.append({
            "id": traj_id,
            "title": meta["title"],
            "type": meta["type"],
            "price_sats": meta["price"],
            "endpoint": f"/api/trajectories/{traj_id}",
        })
    
    return jsonify({
        "catalog": "Overcome Stress. Step by Step.",
        "author": "Sieto Reitsma",
        "methodology": "Corpus Systemics® | ReAttach Therapy",
        "skills": skills,
        "trajectories": trajectories,
        "total_sats_full_program": 1025,
        "payment_protocol": "L402 via Lightning Network",
    })


@app.route("/api/skills/<skill_id>/preview", methods=["GET"])
def skill_preview(skill_id):
    """Free preview: returns metadata and first section only."""
    skill_id = skill_id.upper()
    
    if skill_id not in SKILL_REGISTRY:
        return jsonify({"error": "Skill not found"}), 404
    
    meta = SKILL_REGISTRY[skill_id]
    content = load_skill_content(skill_id)
    
    if not content:
        return jsonify({"error": "Skill content unavailable"}), 500
    
    # Extract first section only (up to second ## heading)
    lines = content.split("\n")
    preview_lines = []
    section_count = 0
    for line in lines:
        if line.startswith("## ") and section_count > 0:
            break
        if line.startswith("## "):
            section_count += 1
        preview_lines.append(line)
    
    preview = "\n".join(preview_lines)
    preview += f"\n\n---\n*[Preview only. Full content: {meta['price']} sats via L402 at /api/skills/{skill_id}]*"
    
    return jsonify({
        "id": skill_id,
        "title": meta["title"],
        "type": meta["type"],
        "price_sats": meta["price"],
        "preview": preview,
        "full_content_endpoint": f"/api/skills/{skill_id}",
        "payment_protocol": "L402",
    })


@app.route("/api/skills/<skill_id>", methods=["GET"])
def get_skill(skill_id):
    """L402-gated skill endpoint with structured data support."""
    skill_id = skill_id.upper()
    if skill_id not in SKILL_REGISTRY:
        return jsonify({"error": "Skill not found"}), 404

    meta = SKILL_REGISTRY[skill_id]
    output_format = request.args.get("format", "full").lower()
    auth_header = request.headers.get("Authorization", "")

    if auth_header:
        result = verify_l402_auth(auth_header)
        if result["valid"]:
            if result["skill_id"] != skill_id:
                return jsonify({"error": "Token not valid for this skill"}), 403
            content = load_skill_content(skill_id)
            if not content:
                return jsonify({"error": "Content unavailable"}), 500

            response_data = {
                "id": skill_id,
                "title": meta["title"],
                "type": meta["type"],
                "payment_hash": result["payment_hash"],
                "version": "1.0",
            }

            if output_format == "raw":
                response_data["content"] = content
            elif output_format == "structured":
                structured = parse_skill_content(content, skill_id)
                response_data["structured"] = structured
            else:
                structured = parse_skill_content(content, skill_id)
                response_data["content"] = content
                response_data["structured"] = structured
                response_data["routing"] = {
                    "trigger_conditions": structured["trigger_conditions"],
                    "prerequisites": structured["prerequisites"],
                    "next_steps": structured["next_steps"],
                    "contraindications": structured["contraindications"],
                }
            return jsonify(response_data)
        else:
            return jsonify({"error": result.get("error", "Invalid authorization")}), 401

    invoice = create_invoice(
        amount_sats=meta["price"],
        memo="Overcome Stress Skill: " + skill_id + " - " + meta["title"]
    )
    if not invoice:
        return jsonify({"error": "Payment service unavailable"}), 503

    macaroon = create_macaroon(invoice["payment_hash"], skill_id)
    payment_store[invoice["payment_hash"]] = {
        "skill_id": skill_id, "paid": False,
        "created": time.time(), "amount": meta["price"],
    }
    save_payments()

    resp_data = {
        "status": 402, "message": "Payment required",
        "skill": {"id": skill_id, "title": meta["title"], "price_sats": meta["price"], "type": meta["type"]},
        "invoice": {"payment_request": invoice["payment_request"], "payment_hash": invoice["payment_hash"], "amount_sats": invoice["amount"]},
        "macaroon": macaroon,
        "instructions": "Pay the Lightning invoice, then retry with header: Authorization: L402 {macaroon}:{preimage}",
        "formats_available": ["full", "structured", "raw"],
    }

    response = Response(json.dumps(resp_data), status=402, mimetype="application/json")
    response.headers["WWW-Authenticate"] = 'L402 macaroon="' + macaroon + '", invoice="' + invoice["payment_request"] + '"'
    return response


@app.route("/api/payment/<payment_hash>/status", methods=["GET"])
def payment_status(payment_hash):
    """Check payment status for a given payment hash."""
    paid = check_invoice_paid(payment_hash)
    
    stored = payment_store.get(payment_hash, {})
    
    return jsonify({
        "payment_hash": payment_hash,
        "paid": paid,
        "skill_id": stored.get("skill_id"),
        "amount_sats": stored.get("amount"),
    })


@app.route("/api/trajectories/<traj_id>", methods=["GET"])
def get_trajectory(traj_id):
    """L402-gated trajectory routing endpoint."""
    traj_id = traj_id.upper()
    
    if traj_id not in TRAJECTORY_REGISTRY:
        return jsonify({"error": "Trajectory not found"}), 404
    
    meta = TRAJECTORY_REGISTRY[traj_id]
    
    # Check auth
    auth_header = request.headers.get("Authorization", "")
    
    if auth_header:
        result = verify_l402_auth(auth_header)
        if result["valid"] and result["skill_id"] == traj_id:
            # Serve trajectory content
            return jsonify(get_trajectory_content(traj_id))
        elif auth_header:
            return jsonify({"error": "Invalid authorization"}), 401
    
    # Create invoice
    invoice = create_invoice(
        amount_sats=meta["price"],
        memo=f"Overcome Stress Trajectory: {traj_id} — {meta['title']}"
    )
    
    if not invoice:
        return jsonify({"error": "Payment service unavailable"}), 503
    
    macaroon = create_macaroon(invoice["payment_hash"], traj_id)
    
    payment_store[invoice["payment_hash"]] = {
        "skill_id": traj_id,
        "paid": False,
        "created": time.time(),
        "amount": meta["price"],
    }
    save_payments()
    
    response = Response(
        json.dumps({
            "status": 402,
            "message": "Payment required",
            "trajectory": {
                "id": traj_id,
                "title": meta["title"],
                "price_sats": meta["price"],
            },
            "invoice": {
                "payment_request": invoice["payment_request"],
                "payment_hash": invoice["payment_hash"],
                "amount_sats": invoice["amount"],
            },
            "macaroon": macaroon,
        }),
        status=402,
        mimetype="application/json"
    )
    
    response.headers["WWW-Authenticate"] = (
        f'L402 macaroon="{macaroon}", '
        f'invoice="{invoice["payment_request"]}"'
    )
    
    return response


def get_trajectory_content(traj_id: str) -> dict:
    """Return full trajectory content from markdown file (structured)."""
    result = load_trajectory(traj_id)
    if result:
        return result
    # Fallback to basic info from registry
    meta = TRAJECTORY_REGISTRY.get(traj_id, {})
    return {
        "id": traj_id,
        "title": meta.get("title", "Unknown"),
        "error": "Detailed trajectory content not available. Basic routing only.",
        "price_sats": meta.get("price", 150),
    }

@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok", "timestamp": int(time.time())})


@app.route("/api/stats", methods=["GET"])
def stats():
    """Public stats (no sensitive data)."""
    cleanup_expired_payments()
    total_paid = sum(1 for p in payment_store.values() if p.get("paid"))
    total_sats = sum(p.get("amount", 0) for p in payment_store.values() if p.get("paid"))
    
    return jsonify({
        "total_skills": len(SKILL_REGISTRY),
        "total_trajectories": len(TRAJECTORY_REGISTRY),
        "total_payments_24h": total_paid,
        "total_sats_24h": total_sats,
    })


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    load_payments()
    print(f"""
╔══════════════════════════════════════════════════════╗
║  Overcome Stress — L402 Skill Server                ║
║  16 Skills | 4 Trajectories | Lightning Micropayments║
║  Port: {PORT}                                         ║
║  Author: Sieto Reitsma                              ║
║  Protocol: L402 (HTTP 402 + Lightning Network)      ║
╚══════════════════════════════════════════════════════╝
    """)
    app.run(host="0.0.0.0", port=PORT, debug=False)
