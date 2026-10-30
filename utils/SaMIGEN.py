#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Integrated SaMIGEN using Kaddu YT generator backend."""

import sys
import os

# Add free_script_extracted to path to import core functions
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../free_script_extracted")))

try:
    from main import create_account, get_device_public_ip
    import requests
except ImportError:
    create_account = None

def generate_guest(region="IND", count=1):
    results = []
    if not create_account:
        return [{"uid": "18945432400", "token": "mock_token_123"}]
    
    class DummySession:
        pass
    
    session = DummySession()
    for _ in range(int(count)):
        try:
            uid, token, name, err = create_account(session, custom_name="sahil_user", custom_password="sahil_pass")
            if uid and token:
                results.append({"uid": str(uid), "token": str(token), "name": name})
        except Exception as e:
            print(f"[!] Generator error: {e}")
    return results
