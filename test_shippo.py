#!/usr/bin/env python
"""
Test script for Shippo integration.
Run: python test_shippo.py
"""
import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'magazinsrebro.settings')
django.setup()

import shippo
from ecommerce.models import Order

# Test API Key
TEST_API_KEY = "shippo_test_e733b0fcadedc161da17d27c1c0f9d83bb7aaae7"

def test_shippo_address():
    """Test creating addresses in Shippo."""
    print("🧪 Testing Shippo Address Creation...")
    
    shippo.config.api_key = TEST_API_KEY
    
    try:
        # Test from address (shop)
        from_address = shippo.Address.create(
            name="Marbaras Test",
            street1="Test Street 123",
            city="Sofia",
            zip="1000",
            country="BG",
            phone="+359888123456",
        )
        print(f"✅ From address created: {from_address.object_id}")
        
        # Test to address (customer)
        to_address = shippo.Address.create(
            name="Test Customer",
            street1="Customer Street 456",
            city="London",
            zip="SW1A 1AA",
            country="GB",
            phone="+44123456789",
        )
        print(f"✅ To address created: {to_address.object_id}")
        
        return from_address, to_address
        
    except Exception as e:
        print(f"❌ Error creating addresses: {e}")
        return None, None


def test_shippo_parcel():
    """Test creating a parcel in Shippo."""
    print("\n🧪 Testing Shippo Parcel Creation...")
    
    shippo.config.api_key = TEST_API_KEY
    
    try:
        parcel = shippo.Parcel.create(
            length='20',
            width='15',
            height='10',
            distance_unit='cm',
            weight='17.6',  # ~0.5 kg in oz
            mass_unit='oz',
        )
        print(f"✅ Parcel created: {parcel.object_id}")
        return parcel
        
    except Exception as e:
        print(f"❌ Error creating parcel: {e}")
        return None


def test_shippo_shipment():
    """Test creating a shipment and getting rates."""
    print("\n🧪 Testing Shippo Shipment Creation...")
    
    shippo.config.api_key = TEST_API_KEY
    
    from_address, to_address = test_shippo_address()
    if not from_address or not to_address:
        return None
    
    parcel = test_shippo_parcel()
    if not parcel:
        return None
    
    try:
        # Create shipment
        shipment = shippo.Shipment.create(
            address_from=from_address,
            address_to=to_address,
            parcels=[parcel],
            async_=False,
        )
        
        print(f"✅ Shipment created: {shipment.object_id}")
        
        # Get rates
        rates = shipment.rates
        if rates:
            print(f"\n📦 Available rates ({len(rates)}):")
            for rate in rates[:5]:  # Show first 5 rates
                print(f"  - {rate.provider} {rate.servicelevel.name}: ${rate.amount}")
            
            # Select cheapest rate
            selected_rate = min(rates, key=lambda r: float(r.amount))
            print(f"\n✅ Selected rate: {selected_rate.provider} {selected_rate.servicelevel.name} - ${selected_rate.amount}")
            
            return shipment, selected_rate
        else:
            print("❌ No rates available")
            return None, None
            
    except Exception as e:
        print(f"❌ Error creating shipment: {e}")
        import traceback
        traceback.print_exc()
        return None, None


def test_shippo_transaction():
    """Test purchasing a label (this will create a real test transaction)."""
    print("\n🧪 Testing Shippo Transaction (Label Purchase)...")
    
    shipment, selected_rate = test_shippo_shipment()
    if not shipment or not selected_rate:
        print("❌ Cannot create transaction without shipment and rate")
        return None
    
    shippo.config.api_key = TEST_API_KEY
    
    try:
        # Purchase label
        transaction = shippo.Transaction.create(
            rate=selected_rate.object_id,
            label_format='PDF',
            async_=False,
        )
        
        print(f"✅ Transaction created: {transaction.object_id}")
        print(f"📦 Tracking: {transaction.tracking_number}")
        print(f"📄 Label URL: {transaction.label_url}")
        
        return transaction
        
    except Exception as e:
        print(f"❌ Error creating transaction: {e}")
        import traceback
        traceback.print_exc()
        return None


if __name__ == "__main__":
    print("=" * 60)
    print("🚀 Shippo Integration Test")
    print("=" * 60)
    
    # Test 1: Addresses
    test_shippo_address()
    
    # Test 2: Parcel
    test_shippo_parcel()
    
    # Test 3: Shipment & Rates
    test_shippo_shipment()
    
    # Test 4: Transaction (Label Purchase) - Uncomment to test actual label purchase
    # print("\n⚠️  Uncomment the next line to test actual label purchase (will use test credits)")
    # test_shippo_transaction()
    
    print("\n" + "=" * 60)
    print("✅ Test completed!")
    print("=" * 60)
