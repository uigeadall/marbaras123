"""
Shipping carrier integrations for FedEx, DHL, and Deutsche Post.
"""
import logging
import requests
from typing import Optional, Dict, Any
from decimal import Decimal
from django.conf import settings

logger = logging.getLogger(__name__)


class ShippingCarrierBase:
    """Base class for shipping carrier integrations."""
    
    def __init__(self):
        self.api_key = None
        self.api_secret = None
        self.account_number = None
    
    def create_shipment(self, order) -> Optional[Dict[str, Any]]:
        """
        Create a shipment and return tracking number and label URL.
        Returns dict with: {'tracking_number': str, 'label_url': str, 'shipment_id': str}
        """
        raise NotImplementedError("Subclasses must implement create_shipment")
    
    def get_label(self, shipment_id: str) -> Optional[str]:
        """Get shipping label URL for existing shipment."""
        raise NotImplementedError("Subclasses must implement get_label")


class FedExShipping(ShippingCarrierBase):
    """FedEx shipping integration."""
    
    def __init__(self):
        super().__init__()
        self.api_key = getattr(settings, 'FEDEX_API_KEY', '')
        self.api_secret = getattr(settings, 'FEDEX_API_SECRET', '')
        self.account_number = getattr(settings, 'FEDEX_ACCOUNT_NUMBER', '')
        self.meter_number = getattr(settings, 'FEDEX_METER_NUMBER', '')
        self.api_url = getattr(settings, 'FEDEX_API_URL', 'https://apis.fedex.com/ship/v1/shipments')
    
    def create_shipment(self, order) -> Optional[Dict[str, Any]]:
        """Create FedEx shipment and return tracking info."""
        logger.info(f"FedEx create_shipment called for Order #{order.id}")
        logger.info(f"FedEx API URL: {self.api_url}")
        logger.info(f"FedEx API Key present: {bool(self.api_key)}")
        logger.info(f"FedEx API Secret present: {bool(self.api_secret)}")
        
        # For sandbox, account_number might not be required
        if not all([self.api_key, self.api_secret]):
            logger.error("FedEx credentials not configured - missing API key or secret")
            return None
        if not self.account_number:
            logger.warning("FedEx account_number not configured - may fail for production")
        
        try:
            # Get OAuth token
            logger.info("Requesting FedEx OAuth token...")
            token = self._get_access_token()
            if not token:
                logger.error("Failed to obtain FedEx OAuth token")
                return None
            logger.info("✅ FedEx OAuth token obtained")
            
            # Prepare shipment data
            logger.info("Preparing shipment data...")
            shipment_data = self._prepare_shipment_data(order)
            logger.debug(f"Shipment data prepared: {shipment_data}")
            
            # Create shipment via FedEx API
            headers = {
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
                'X-locale': 'en_US'
            }
            
            logger.info(f"Posting to FedEx API: {self.api_url}")
            response = requests.post(
                self.api_url,
                json=shipment_data,
                headers=headers,
                timeout=30
            )
            
            logger.info(f"FedEx API response status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                logger.info(f"FedEx API response data: {data}")
                output = data.get('output', {})
                tracking_number = output.get('transactionShipments', [{}])[0].get('masterTrackingNumber', '')
                label_url = output.get('labelDocuments', [{}])[0].get('url', '')
                shipment_id = output.get('jobId', '')
                
                logger.info(f"✅ FedEx shipment created: tracking={tracking_number}, label_url={label_url}")
                
                return {
                    'tracking_number': tracking_number,
                    'label_url': label_url,
                    'shipment_id': shipment_id,
                }
            else:
                logger.error(f"FedEx API error: {response.status_code}")
                logger.error(f"FedEx API error response: {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"FedEx shipment creation exception: {e}", exc_info=True)
            return None
    
    def _get_access_token(self) -> Optional[str]:
        """Get OAuth access token from FedEx."""
        try:
            token_url = 'https://apis.fedex.com/oauth/token'
            data = {
                'grant_type': 'client_credentials',
                'client_id': self.api_key,
                'client_secret': self.api_secret
            }
            response = requests.post(token_url, data=data, timeout=10)
            if response.status_code == 200:
                return response.json().get('access_token')
            return None
        except Exception as e:
            logger.error(f"FedEx token request failed: {e}")
            return None
    
    def _prepare_shipment_data(self, order) -> Dict[str, Any]:
        """Prepare shipment data for FedEx API."""
        # Get order weight (estimate based on items)
        total_weight = sum(item.quantity for item in order.items.all()) * 0.5  # Estimate 0.5kg per item
        
        return {
            'labelResponseOptions': 'URL_ONLY',
            'requestedShipment': {
                'shipper': {
                    'contact': {
                        'personName': 'Marbaras',
                        'phoneNumber': getattr(settings, 'SHOP_PHONE', ''),
                    },
                    'address': {
                        'streetLines': [getattr(settings, 'SHOP_ADDRESS', '')],
                        'city': getattr(settings, 'SHOP_CITY', 'Sofia'),
                        'stateOrProvinceCode': getattr(settings, 'SHOP_STATE', ''),
                        'postalCode': getattr(settings, 'SHOP_POSTAL_CODE', ''),
                        'countryCode': getattr(settings, 'SHOP_COUNTRY', 'BG'),
                    }
                },
                'recipients': [{
                    'contact': {
                        'personName': order.full_name,
                        'phoneNumber': order.phone,
                    },
                    'address': {
                        'streetLines': [order.address],
                        'city': order.city,
                        'postalCode': order.postal_code,
                        'countryCode': order.country or 'BG',
                    }
                }],
                'shipDatestamp': order.created_at.strftime('%Y-%m-%d'),
                'serviceType': 'STANDARD_OVERNIGHT',
                'packagingType': 'YOUR_PACKAGING',
                'pickupType': 'USE_SCHEDULED_PICKUP',
                'blockInsightVisibility': False,
                'shippingChargesPayment': {
                    'paymentType': 'SENDER'
                },
                'labelSpecification': {
                    'imageType': 'PDF',
                    'labelStockType': 'PAPER_4X6'
                },
                'requestedPackageLineItems': [{
                    'weight': {
                        'units': 'KG',
                        'value': max(total_weight, 0.5)  # Minimum 0.5kg
                    }
                }]
            },
            'accountNumber': {
                'value': self.account_number
            }
        }


class DHLShipping(ShippingCarrierBase):
    """DHL shipping integration."""
    
    def __init__(self):
        super().__init__()
        self.api_key = getattr(settings, 'DHL_API_KEY', '')
        self.api_secret = getattr(settings, 'DHL_API_SECRET', '')
        self.account_number = getattr(settings, 'DHL_ACCOUNT_NUMBER', '')
        self.api_url = getattr(settings, 'DHL_API_URL', 'https://api-eu.dhl.com/shipment/shipments')
    
    def create_shipment(self, order) -> Optional[Dict[str, Any]]:
        """Create DHL shipment and return tracking info."""
        if not all([self.api_key, self.api_secret, self.account_number]):
            logger.error("DHL credentials not configured")
            return None
        
        try:
            # Prepare shipment data
            shipment_data = self._prepare_shipment_data(order)
            
            # Create shipment via DHL API
            headers = {
                'DHL-API-Key': self.api_key,
                'Content-Type': 'application/json',
            }
            
            auth = (self.api_key, self.api_secret)
            
            response = requests.post(
                self.api_url,
                json=shipment_data,
                headers=headers,
                auth=auth,
                timeout=30
            )
            
            if response.status_code in [200, 201]:
                data = response.json()
                return {
                    'tracking_number': data.get('shipmentTrackingNumber', ''),
                    'label_url': data.get('label', {}).get('b64Content', ''),
                    'shipment_id': data.get('shipmentTrackingNumber', ''),
                }
            else:
                logger.error(f"DHL API error: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"DHL shipment creation failed: {e}")
            return None
    
    def _prepare_shipment_data(self, order) -> Dict[str, Any]:
        """Prepare shipment data for DHL API."""
        total_weight = sum(item.quantity for item in order.items.all()) * 0.5
        
        return {
            'plannedShippingDateAndTime': order.created_at.strftime('%Y-%m-%dT%H:%M:%S'),
            'pickup': {
                'isRequested': False
            },
            'productCode': 'N',
            'accounts': [{
                'typeCode': 'shipper',
                'number': self.account_number
            }],
            'outputImageProperties': {
                'printerDPI': 300,
                'encodingFormat': 'PDF',
                'imageOptions': [{
                    'typeCode': 'label',
                    'templateName': 'ECOM26_84_001'
                }]
            },
            'customerDetails': {
                'shipperDetails': {
                    'postalAddress': {
                        'postalCode': getattr(settings, 'SHOP_POSTAL_CODE', ''),
                        'cityName': getattr(settings, 'SHOP_CITY', 'Sofia'),
                        'countryCode': getattr(settings, 'SHOP_COUNTRY', 'BG'),
                        'addressLine1': getattr(settings, 'SHOP_ADDRESS', ''),
                    },
                    'contactInformation': {
                        'phone': getattr(settings, 'SHOP_PHONE', ''),
                        'email': getattr(settings, 'SHOP_EMAIL', ''),
                        'companyName': 'Marbaras'
                    }
                },
                'receiverDetails': {
                    'postalAddress': {
                        'postalCode': order.postal_code,
                        'cityName': order.city,
                        'countryCode': order.country or 'BG',
                        'addressLine1': order.address,
                    },
                    'contactInformation': {
                        'phone': order.phone,
                        'email': order.email or '',
                        'fullName': order.full_name
                    }
                }
            },
            'content': {
                'packages': [{
                    'weight': max(total_weight, 0.5),
                    'dimensions': {
                        'length': 20,
                        'width': 15,
                        'height': 10
                    }
                }]
            }
        }


class DeutschePostShipping(ShippingCarrierBase):
    """Deutsche Post / DHL Parcel integration."""
    
    def __init__(self):
        super().__init__()
        self.api_key = getattr(settings, 'DEUTSCHE_POST_API_KEY', '')
        self.api_secret = getattr(settings, 'DEUTSCHE_POST_API_SECRET', '')
        self.account_number = getattr(settings, 'DEUTSCHE_POST_ACCOUNT_NUMBER', '')
        self.api_url = getattr(settings, 'DEUTSCHE_POST_API_URL', 'https://api-sandbox.dhl.com/parcel/de/shipping/v2/orders')
    
    def create_shipment(self, order) -> Optional[Dict[str, Any]]:
        """Create Deutsche Post shipment and return tracking info."""
        if not all([self.api_key, self.api_secret, self.account_number]):
            logger.error("Deutsche Post credentials not configured")
            return None
        
        try:
            # Get OAuth token
            token = self._get_access_token()
            if not token:
                return None
            
            # Prepare shipment data
            shipment_data = self._prepare_shipment_data(order)
            
            # Create shipment via Deutsche Post API
            headers = {
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }
            
            response = requests.post(
                self.api_url,
                json=shipment_data,
                headers=headers,
                timeout=30
            )
            
            if response.status_code in [200, 201]:
                data = response.json()
                return {
                    'tracking_number': data.get('shipmentNo', ''),
                    'label_url': data.get('label', {}).get('b64', ''),
                    'shipment_id': data.get('shipmentNo', ''),
                }
            else:
                logger.error(f"Deutsche Post API error: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Deutsche Post shipment creation failed: {e}")
            return None
    
    def _get_access_token(self) -> Optional[str]:
        """Get OAuth access token from Deutsche Post."""
        try:
            token_url = 'https://api-sandbox.dhl.com/parcel/de/account/auth/v1/accesstoken'
            auth = (self.api_key, self.api_secret)
            response = requests.post(token_url, auth=auth, timeout=10)
            if response.status_code == 200:
                return response.json().get('accessToken')
            return None
        except Exception as e:
            logger.error(f"Deutsche Post token request failed: {e}")
            return None
    
    def _prepare_shipment_data(self, order) -> Dict[str, Any]:
        """Prepare shipment data for Deutsche Post API."""
        total_weight = sum(item.quantity for item in order.items.all()) * 0.5
        
        return {
            'product': 'V01PAK',
            'billingNumber': self.account_number,
            'refNo': f'ORDER-{order.id}',
            'shipDate': order.created_at.strftime('%Y-%m-%d'),
            'shipper': {
                'name1': 'Marbaras',
                'addressStreet': getattr(settings, 'SHOP_ADDRESS', ''),
                'addressHouse': '',
                'postalCode': getattr(settings, 'SHOP_POSTAL_CODE', ''),
                'city': getattr(settings, 'SHOP_CITY', 'Sofia'),
                'country': getattr(settings, 'SHOP_COUNTRY', 'BG'),
                'email': getattr(settings, 'SHOP_EMAIL', ''),
                'phone': getattr(settings, 'SHOP_PHONE', ''),
            },
            'consignee': {
                'name1': order.full_name,
                'addressStreet': order.address,
                'postalCode': order.postal_code,
                'city': order.city,
                'country': order.country or 'BG',
                'email': order.email or '',
                'phone': order.phone,
            },
            'details': {
                'weight': max(total_weight, 0.5),
                'dim': {
                    'uom': 'cm',
                    'height': 10,
                    'length': 20,
                    'width': 15
                }
            }
        }


def get_shipping_carrier(carrier_name: str) -> Optional[ShippingCarrierBase]:
    """Factory function to get shipping carrier instance."""
    carriers = {
        'fedex': FedExShipping,
        'dhl': DHLShipping,
        'deutsche_post': DeutschePostShipping,
    }
    
    carrier_class = carriers.get(carrier_name.lower())
    if carrier_class:
        return carrier_class()
    return None


def create_shipping_label(order, carrier_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Create shipping label for an order.
    If carrier_name is not provided, tries to determine from shipping_option.
    """
    # Determine carrier from shipping option or use provided
    if not carrier_name and order.shipping_option:
        # Map shipping option names to carriers
        option_name = order.shipping_option.name.lower()
        if 'fedex' in option_name:
            carrier_name = 'fedex'
        elif 'dhl' in option_name:
            carrier_name = 'dhl'
        elif 'deutsche' in option_name or 'post' in option_name:
            carrier_name = 'deutsche_post'
    
    if not carrier_name:
        logger.warning(f"No carrier specified for order {order.id}")
        return None
    
    carrier = get_shipping_carrier(carrier_name)
    if not carrier:
        logger.error(f"Unknown carrier: {carrier_name}")
        return None
    
    return carrier.create_shipment(order)

