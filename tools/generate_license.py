import json
import hashlib
import os
from datetime import datetime, timedelta

# ==========================================
# 🔐 VISVA DATA - MASTER KEY GENERATOR
# ==========================================

MASTER_SECRET_SALT = "VISVA_SECRET_SALT" 

def generate_enterprise_key():
    print("=== 💎 VISVA DATA ENTERPRISE KEYGEN ===")
    
    client_id = input("Enter Client/Company Name (e.g., Google_Corp): ").strip()
    days_valid = int(input("Enter License Validity in Days (e.g., 365): "))
    
    expiry_date = (datetime.now() + timedelta(days=days_valid)).strftime('%Y-%m-%d')
    
    raw_string = f"{client_id}:{expiry_date}:{MASTER_SECRET_SALT}"
    digital_signature = hashlib.sha256(raw_string.encode()).hexdigest()
    
    key_data = {
        "client_id": client_id,
        "issued_on": datetime.now().strftime('%Y-%m-%d'),
        "expiry": expiry_date,
        "signature": digital_signature,
        "warning": "DO NOT MODIFY THIS FILE. TAMPERING WILL LOCK THE NEURAL ENGINE."
    }
    
    # CRITICAL FIX: Creates a dedicated deployment folder automatically
    folder_name = f"Deploy_{client_id}"
    os.makedirs(folder_name, exist_ok=True)
    
    # Saves exactly as 'visva_enterprise.key' so Compose finds it instantly
    filepath = os.path.join(folder_name, "visva_enterprise.key")
    with open(filepath, 'w') as f:
        json.dump(key_data, f, indent=4)
        
    print(f"\n✅ SUCCESS: Enterprise License Key Generated!")
    print(f"📁 Saved in folder: {folder_name}/")
    print(f"🔑 File name: visva_enterprise.key")
    print(f"⏳ Expires on: {expiry_date}")
    print("\n📦 NEXT STEP: Put your 'docker-compose.yml' in that folder, Zip it, and send to client.")

if __name__ == "__main__":
    generate_enterprise_key()