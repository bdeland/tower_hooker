#!/usr/bin/env python3
"""
Simple validation script to check if .env file is properly configured.
Run this before starting the infrastructure to catch configuration issues early.
"""

import os
from pathlib import Path

def validate_env_file():
    """Validate that .env file exists and contains required variables."""
    
    env_file = Path('.env')
    env_example = Path('env.example')
    
    print("🔍 Validating environment configuration...")
    
    # Check if .env file exists
    if not env_file.exists():
        print("❌ .env file not found!")
        if env_example.exists():
            print(f"💡 Copy {env_example} to .env and edit with your credentials:")
            print(f"   cp {env_example} .env")
        else:
            print("❌ env.example template not found either!")
        return False
    
    # Load environment variables from .env
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        print("⚠️  python-dotenv not installed, checking environment variables directly")
    
    # Required environment variables (only those that should be in .env)
    required_vars = [
        # Application configuration
        'LOG_LEVEL',
        # File paths
        'BLUESTACKS_ADB_PATH',
        # Frida configuration
        'FRIDA_SERVER_VERSION',
        'FRIDA_SERVER_ARCH',
        # Database schema
        'DB_SCHEMA_VALIDATION_ENABLED',
        'DB_SCHEMA_STRICT_MODE',
        # InfluxDB configuration
        'INFLUXDB_ORG',
        'INFLUXDB_TOKEN',
        'INFLUXDB_BUCKET',
        # Credentials
        'INFLUXDB_USERNAME', 
        'INFLUXDB_PASSWORD',
        'GRAFANA_ADMIN_PASSWORD',
        # Docker initialization
        'DOCKER_INFLUXDB_INIT_USERNAME',
        'DOCKER_INFLUXDB_INIT_PASSWORD',
        'DOCKER_INFLUXDB_INIT_ADMIN_TOKEN',
        'DOCKER_GRAFANA_ADMIN_PASSWORD'
    ]
    
    missing_vars = []
    default_vars = []
    
    for var in required_vars:
        value = os.getenv(var)
        if not value:
            missing_vars.append(var)
        elif value in [
            'tower_hooker_token_123', 'admin', 'password123', 'admin123',
            'tower_hooker', 'metrics',
            'INFO', 
            'C:\\Program Files\\BlueStacks_nxt\\HD-Adb.exe',
            '16.7.19', 'x86_64',
            'true', 'false'
        ]:
            default_vars.append(var)
    
    # Report results
    if missing_vars:
        print(f"❌ Missing required environment variables: {', '.join(missing_vars)}")
        return False
    
    if default_vars:
        print(f"⚠️  Using default values for: {', '.join(default_vars)}")
        print("   Consider changing these for production use")
    
    # Check consistency between related variables
    consistency_checks = [
        ('INFLUXDB_TOKEN', 'DOCKER_INFLUXDB_INIT_ADMIN_TOKEN'),
        ('INFLUXDB_USERNAME', 'DOCKER_INFLUXDB_INIT_USERNAME'),
        ('INFLUXDB_PASSWORD', 'DOCKER_INFLUXDB_INIT_PASSWORD'),
        ('GRAFANA_ADMIN_PASSWORD', 'DOCKER_GRAFANA_ADMIN_PASSWORD')
    ]
    
    inconsistent = []
    for var1, var2 in consistency_checks:
        if os.getenv(var1) != os.getenv(var2):
            inconsistent.append(f"{var1} != {var2}")
    
    if inconsistent:
        print(f"❌ Inconsistent values: {', '.join(inconsistent)}")
        print("   These pairs should have matching values")
        return False
    
    print("✅ Environment configuration looks good!")
    return True

if __name__ == "__main__":
    success = validate_env_file()
    exit(0 if success else 1) 