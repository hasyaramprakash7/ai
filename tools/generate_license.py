#!/usr/bin/env python3
import json
import hashlib
import os
from datetime import datetime, timedelta

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
    
    folder_name = f"Deploy_{client_id}"
    os.makedirs(folder_name, exist_ok=True)
    
    key_filepath = os.path.join(folder_name, "visva_enterprise.key")
    with open(key_filepath, 'w') as f:
        json.dump(key_data, f, indent=4)
    
    env_path = ".env"
    env_content = f"VISVA_API_KEY={digital_signature}\n"
    with open(env_path, "w") as f:
        f.write(env_content)
    
    print(f"\n✅ SUCCESS: Enterprise License Key Generated!")
    print(f"📁 License file: {folder_name}/visva_enterprise.key")
    print(f"📁 Environment file: .env (overwritten with VISVA_API_KEY)")
    print(f"⏳ Expires on: {expiry_date}")
    print("\n📦 Place the .key file in the project root and run 'docker-compose up'.")

if __name__ == "__main__":
    generate_enterprise_key()