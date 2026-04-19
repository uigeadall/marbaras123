"""
Shipping carrier integrations for FedEx, DHL, Deutsche Post, and EasyPost.
"""
import logging
import requests
from typing import Optional, Dict, Any
from decimal import Decimal
from django.conf import settings

try:
    import easypost
except ImportError:
    easypost = None

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
        # Ensure API URL includes the full path
        # Default to PRODUCTION URL (not sandbox) for real orders
        api_url = getattr(settings, 'FEDEX_API_URL', 'https://apis.fedex.com/ship/v1/shipments')
        # If URL doesn't end with /ship/v1/shipments, add it
        if api_url and not api_url.endswith('/ship/v1/shipments'):
            if api_url.endswith('/'):
                api_url = api_url.rstrip('/')
            if 'sandbox' in api_url.lower():
                api_url = 'https://apis-sandbox.fedex.com/ship/v1/shipments'
            else:
                # Use PRODUCTION URL for real orders
                api_url = 'https://apis.fedex.com/ship/v1/shipments'
        self.api_url = api_url
        logger.info(f"FedEx API URL configured: {self.api_url} ({'PRODUCTION' if 'sandbox' not in self.api_url.lower() else 'SANDBOX'})")
    
    def create_shipment(self, order) -> Optional[Dict[str, Any]]:
        """Create FedEx shipment and return tracking info."""
        logger.info(f"FedEx create_shipment called for Order #{order.id}")
        logger.info(f"FedEx API URL: {self.api_url}")
        logger.info(f"FedEx API Key present: {bool(self.api_key)}")
        logger.info(f"FedEx API Secret present: {bool(self.api_secret)}")
        logger.info(f"FedEx Account Number present: {bool(self.account_number)}")
        if self.account_number:
            logger.info(f"FedEx Account Number: {self.account_number}")
        
        # Check required credentials
        if not all([self.api_key, self.api_secret]):
            logger.error("FedEx credentials not configured - missing API key or secret")
            return None
        if not self.account_number:
            logger.error("FedEx account_number is REQUIRED - even for sandbox. Please add FEDEX_ACCOUNT_NUMBER in Railway variables.")
            logger.error("You can find your test account number in FedEx Developer Portal -> Project Settings or Account Information")
            return None
        
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
            
            # Log request body for debugging (first 3000 chars)
            import json
            try:
                request_body_str = json.dumps(shipment_data, indent=2)
                logger.info(f"FedEx API request body (first 3000 chars):\n{request_body_str[:3000]}")
                # Also log specific sections
                if 'accountNumber' in shipment_data:
                    logger.info(f"Top-level accountNumber: {shipment_data['accountNumber']}")
                if 'requestedShipment' in shipment_data and 'shippingChargesPayment' in shipment_data['requestedShipment']:
                    if 'payor' in shipment_data['requestedShipment']['shippingChargesPayment']:
                        logger.info(f"payor section: {json.dumps(shipment_data['requestedShipment']['shippingChargesPayment']['payor'], indent=2)}")
            except Exception as e:
                logger.error(f"Error logging request body: {e}")
            
            if response.status_code == 200:
                # Initialize variables
                tracking_number = ''
                label_url = ''
                shipment_id = ''
                
                try:
                    data = response.json()
                    logger.info(f"FedEx API response status: {response.status_code}")
                    logger.info(f"FedEx API response keys: {list(data.keys())}")
                    
                    output = data.get('output', {})
                    logger.info(f"Output keys: {list(output.keys()) if isinstance(output, dict) else 'Not a dict'}")
                    
                    # Extract tracking number
                    transaction_shipments = output.get('transactionShipments', [])
                    if transaction_shipments:
                        tracking_number = transaction_shipments[0].get('masterTrackingNumber', '')
                        logger.info(f"Found transactionShipments: {len(transaction_shipments)}")
                        logger.info(f"First shipment keys: {list(transaction_shipments[0].keys()) if transaction_shipments[0] else 'Empty'}")
                        logger.info(f"First shipment data: {transaction_shipments[0]}")
                    else:
                        logger.warning("No transactionShipments found in output")
                    
                    # Extract label URL - check multiple possible locations
                    label_documents = output.get('labelDocuments', [])
                    logger.info(f"labelDocuments in output: {len(label_documents)}")
                    if label_documents:
                        label_url = label_documents[0].get('url', '')
                        logger.info(f"Found labelDocuments: {len(label_documents)}")
                        logger.info(f"First label document keys: {list(label_documents[0].keys()) if label_documents[0] else 'Empty'}")
                        logger.info(f"First label document: {label_documents[0]}")
                    else:
                        # Try alternative location
                        logger.info("Checking transactionShipments for labelDocuments...")
                        if transaction_shipments:
                            label_docs = transaction_shipments[0].get('labelDocuments', [])
                            logger.info(f"labelDocuments in transactionShipments: {len(label_docs)}")
                            if label_docs:
                                label_url = label_docs[0].get('url', '')
                                logger.info(f"Found labelDocuments in transactionShipments: {label_docs[0]}")
                            else:
                                logger.warning("No labelDocuments found in transactionShipments either")
                    
                    # Extract shipment ID
                    shipment_id = output.get('jobId', '')
                    logger.info(f"jobId in output: {shipment_id}")
                    if not shipment_id and transaction_shipments:
                        shipment_id = transaction_shipments[0].get('shipmentId', '')
                        logger.info(f"shipmentId in transactionShipments: {shipment_id}")
                    
                    logger.info(f"✅ FedEx shipment created: tracking={tracking_number}, label_url={label_url or 'N/A'}, shipment_id={shipment_id or 'N/A'}")
                    
                except Exception as e:
                    logger.error(f"Error parsing FedEx response: {e}", exc_info=True)
                    raise
                
                return {
                    'tracking_number': tracking_number or None,
                    'label_url': label_url or None,
                    'shipment_id': shipment_id or None,
                }
            else:
                logger.error(f"FedEx API error: {response.status_code}")
                logger.error(f"FedEx API response headers: {dict(response.headers)}")
                logger.error(f"FedEx API response text (FULL): {response.text}")
                try:
                    error_data = response.json()
                    logger.error(f"FedEx API error response (JSON): {error_data}")
                    if 'errors' in error_data:
                        logger.error(f"Number of errors: {len(error_data['errors'])}")
                        for idx, error in enumerate(error_data['errors'], 1):
                            logger.error(f"  Error #{idx}:")
                            logger.error(f"    Code: {error.get('code')}")
                            logger.error(f"    Message: {error.get('message')}")
                            if 'parameterList' in error:
                                logger.error(f"    Parameters: {error.get('parameterList')}")
                    # Also check for 'transactionId' and other fields
                    if 'transactionId' in error_data:
                        logger.error(f"Transaction ID: {error_data.get('transactionId')}")
                except Exception as e:
                    logger.error(f"Failed to parse error response as JSON: {e}")
                    logger.error(f"FedEx API error response (raw text): {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"FedEx shipment creation exception: {e}", exc_info=True)
            return None
    
    def _get_access_token(self) -> Optional[str]:
        """Get OAuth access token from FedEx."""
        try:
            # Use sandbox token URL if sandbox API URL is configured
            if 'sandbox' in self.api_url.lower():
                token_url = 'https://apis-sandbox.fedex.com/oauth/token'
            else:
                token_url = 'https://apis.fedex.com/oauth/token'
            
            logger.info(f"Requesting FedEx OAuth token from {token_url}")
            logger.info(f"FedEx API Key (first 10 chars): {self.api_key[:10] if self.api_key else 'EMPTY'}...")
            logger.info(f"FedEx API Secret (first 10 chars): {self.api_secret[:10] if self.api_secret else 'EMPTY'}...")
            logger.info(f"FedEx API Key length: {len(self.api_key) if self.api_key else 0}")
            logger.info(f"FedEx API Secret length: {len(self.api_secret) if self.api_secret else 0}")
            
            # FedEx OAuth requires Basic Auth with API Key as username and Secret as password
            auth = (self.api_key, self.api_secret)
            data = {
                'grant_type': 'client_credentials',
            }
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded'
            }
            logger.info(f"Trying Basic Auth for OAuth token...")
            response = requests.post(token_url, auth=auth, data=data, headers=headers, timeout=10)
            
            # If Basic Auth fails, try form data method
            if response.status_code != 200:
                logger.info(f"Basic Auth failed ({response.status_code}), trying form data method...")
                data = {
                    'grant_type': 'client_credentials',
                    'client_id': self.api_key,
                    'client_secret': self.api_secret
                }
                response = requests.post(token_url, data=data, headers=headers, timeout=10)
            
            if response.status_code == 200:
                token = response.json().get('access_token')
                logger.info("✅ FedEx OAuth token obtained successfully")
                return token
            else:
                logger.error(f"FedEx token request failed: {response.status_code} - {response.text}")
                logger.error(f"FedEx API Key used: {self.api_key[:20]}... (first 20 chars)")
                return None
        except Exception as e:
            logger.error(f"FedEx token request exception: {e}", exc_info=True)
            return None
    
    def _normalize_country_code(self, country: Optional[str]) -> str:
        """Normalize country code to ISO 2-letter format for FedEx."""
        if not country:
            return 'BG'  # Default to Bulgaria
        
        country = country.strip().upper()
        
        # If already a 2-letter code, return as is
        if len(country) == 2:
            return country
        
        # Common country name mappings to ISO codes
        country_mapping = {
            'BULGARIA': 'BG',
            'БЪЛГАРИЯ': 'BG',
            'UNITED STATES': 'US',
            'USA': 'US',
            'UNITED KINGDOM': 'GB',
            'UK': 'GB',
            'GERMANY': 'DE',
            'FRANCE': 'FR',
            'ITALY': 'IT',
            'SPAIN': 'ES',
            'GREECE': 'GR',
            'ROMANIA': 'RO',
            'TURKEY': 'TR',
        }
        
        # Check mapping
        if country in country_mapping:
            return country_mapping[country]
        
        # Default fallback
        logger.warning(f"Unknown country format: {country}, defaulting to BG")
        return 'BG'
    
    def _normalize_postal_code(self, postal_code: str, country_code: str) -> str:
        """Normalize postal code format based on country."""
        if not postal_code:
            return ''
        
        # Remove spaces and non-digit characters
        cleaned = ''.join(filter(str.isdigit, str(postal_code)))
        
        # Country-specific formatting
        if country_code == 'BG':  # Bulgaria - 4 digits
            if len(cleaned) >= 4:
                return cleaned[:4]
            elif len(cleaned) > 0:
                # Pad with zeros if less than 4 digits
                return cleaned.zfill(4)
            else:
                return '1000'  # Default Sofia postal code
        elif country_code == 'GB':  # UK - various formats (e.g., SW1A 1AA, M1 1AA, etc.)
            # UK postal codes can be: SW1A 1AA, M1 1AA, B33 8TH, etc.
            # Remove spaces and convert to uppercase
            uk_code = postal_code.strip().upper().replace(' ', '')
            # If it's a Bulgarian postal code (4 digits), use a default UK code
            if uk_code.isdigit() and len(uk_code) == 4:
                return 'SW1A 1AA'  # Default London postal code for UK
            # If it looks like a valid UK format (6-8 chars with letters and numbers), return as is
            if len(uk_code) >= 5 and len(uk_code) <= 8:
                # Format as UK postal code (e.g., SW1A1AA -> SW1A 1AA)
                if len(uk_code) >= 5:
                    return f"{uk_code[:-3]} {uk_code[-3:]}" if len(uk_code) > 5 else uk_code
            return 'SW1A 1AA'  # Default UK postal code
        elif country_code == 'US':  # USA - 5 or 9 digits
            if len(cleaned) >= 5:
                return cleaned[:5] if len(cleaned) < 9 else f"{cleaned[:5]}-{cleaned[5:9]}"
            else:
                return cleaned.zfill(5)
        else:
            # For other countries, return cleaned version
            return cleaned if cleaned else postal_code.strip()
    
    def _get_service_type(self, order) -> str:
        """Determine appropriate FedEx service type based on origin and destination."""
        # Get origin country from FEDEX_SHIPPING_LOCATION (where FedEx account is registered)
        # If not set, fall back to SHOP_COUNTRY
        origin_country = getattr(settings, 'FEDEX_SHIPPING_LOCATION', None)
        if not origin_country:
            origin_country = getattr(settings, 'SHOP_COUNTRY', 'BG')
        
        # Normalize destination country
        dest_country = self._normalize_country_code(order.country)
        
        logger.info(f"Service type check: origin={origin_country}, destination={dest_country}")
        
        # If domestic shipment (same country)
        if origin_country == dest_country:
            # Use domestic service types
            logger.info(f"Domestic shipment detected: {origin_country} -> {dest_country}, using FEDEX_GROUND")
            return 'FEDEX_GROUND'  # or 'STANDARD_OVERNIGHT' for express
        
        # For international shipments, use international service types
        # INTERNATIONAL_PRIORITY - faster, more widely supported
        # INTERNATIONAL_ECONOMY - slower but cheaper (may not be available for all routes)
        logger.info(f"International shipment detected: {origin_country} -> {dest_country}, using INTERNATIONAL_PRIORITY")
        return 'INTERNATIONAL_PRIORITY'
    
    def _prepare_shipment_data(self, order) -> Dict[str, Any]:
        """Prepare shipment data for FedEx API."""
        # Get order weight (estimate based on items)
        total_weight = sum(item.quantity for item in order.items.all()) * 0.5  # Estimate 0.5kg per item
        
        # Normalize country codes
        recipient_country = self._normalize_country_code(order.country)
        logger.info(f"Order #{order.id} - recipient country: {order.country} -> normalized: {recipient_country}")
        
        # Check if this is an international shipment (for customs value requirement)
        origin_country = getattr(settings, 'FEDEX_SHIPPING_LOCATION', None) or getattr(settings, 'SHOP_COUNTRY', 'BG')
        is_international = origin_country != recipient_country
        
        # Build package line items
        package_item = {
            'weight': {
                'units': 'KG',
                'value': max(total_weight, 0.5)  # Minimum 0.5kg
            }
        }
        
        # Add declared value for international shipments (REQUIRED)
        # FedEx requires minimum value of 1.00 USD for customs
        if is_international:
            customs_amount = max(float(order.total_price), 1.0)  # Minimum 1.00 USD
            package_item['declaredValue'] = {
                'amount': f"{customs_amount:.2f}",
                'currency': 'USD'
            }
        
        # Build requested shipment structure
        requested_shipment = {
            'shipper': {
                'contact': {
                    'personName': 'Marbaras',
                    'phoneNumber': getattr(settings, 'SHOP_PHONE', ''),
                },
                'address': {
                    'streetLines': [getattr(settings, 'SHOP_ADDRESS', '')],
                    'city': getattr(settings, 'SHOP_CITY', 'Sofia'),
                    'stateOrProvinceCode': getattr(settings, 'SHOP_STATE', ''),
                    # Normalize shipper postal code based on shipping location
                    'postalCode': self._normalize_postal_code(
                        getattr(settings, 'SHOP_POSTAL_CODE', ''),
                        getattr(settings, 'FEDEX_SHIPPING_LOCATION', None) or getattr(settings, 'SHOP_COUNTRY', 'BG')
                    ),
                    # Use FEDEX_SHIPPING_LOCATION for shipper country (where FedEx account is registered)
                    # This must match the shipping location in Developer Portal
                    'countryCode': getattr(settings, 'FEDEX_SHIPPING_LOCATION', None) or getattr(settings, 'SHOP_COUNTRY', 'BG'),
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
                        # Validate and format postal code based on country
                        'postalCode': self._normalize_postal_code(order.postal_code, self._normalize_country_code(order.country)),
                        'countryCode': self._normalize_country_code(order.country),
                    }
                }],
            'shipDatestamp': order.created_at.strftime('%Y-%m-%d'),
            # Determine service type based on destination
            # For international shipments, use INTERNATIONAL_ECONOMY or INTERNATIONAL_PRIORITY
            # For domestic shipments, use STANDARD_OVERNIGHT or FEDEX_GROUND
            'serviceType': self._get_service_type(order),
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
            'requestedPackageLineItems': [package_item]
        }
        
        # Add customs clearance detail for international shipments (REQUIRED)
        # FedEx requires minimum value of 1.00 USD for customs
        if is_international:
            customs_amount = max(float(order.total_price), 1.0)  # Minimum 1.00 USD
            customs_amount_str = f"{customs_amount:.2f}"
            
            # Build commodities list from order items (REQUIRED by FedEx)
            commodities = []
            for item in order.items.all():
                # Get item price from product (OrderItem doesn't have price field)
                item_price = 0.0
                if item.product:
                    # Use discounted price if available, otherwise use regular price
                    if hasattr(item.product, 'get_discounted_price'):
                        item_price = float(item.product.get_discounted_price())
                    elif hasattr(item.product, 'price'):
                        item_price = float(item.product.price)
                    elif hasattr(item.product, 'discount_price') and item.product.discount_price:
                        item_price = float(item.product.discount_price)
                
                # Ensure minimum price of 0.01 USD
                item_price = max(item_price, 0.01)
                total_item_value = item_price * item.quantity
                
                product_name = item.product.name[:50] if item.product and hasattr(item.product, 'name') else 'Product'
                
                # Calculate weight per item (distribute total weight proportionally)
                item_weight = max(0.1, total_weight / max(len(order.items.all()), 1))  # Minimum 0.1kg per item
                
                commodities.append({
                    'description': product_name,  # Max 50 chars
                    'quantity': item.quantity,
                    'quantityUnits': 'PCS',  # Pieces
                    'weight': {
                        'units': 'KG',
                        'value': round(item_weight * item.quantity, 2)  # Weight must be numeric
                    },
                    'unitPrice': {
                        'amount': f"{item_price:.2f}",
                        'currency': 'USD'
                    },
                    'customsValue': {
                        'amount': f"{total_item_value:.2f}",
                        'currency': 'USD'
                    },
                    'countryOfManufacture': getattr(settings, 'SHOP_COUNTRY', 'BG'),  # Origin country
                    'harmonizedCode': '7117190000'  # Generic jewelry code (can be customized per product)
                })
            
            # If no items, add a default commodity
            if not commodities:
                commodities.append({
                    'description': 'Jewelry',
                    'quantity': 1,
                    'quantityUnits': 'PCS',
                    'weight': {
                        'units': 'KG',
                        'value': 0.5  # Default weight
                    },
                    'unitPrice': {
                        'amount': customs_amount_str,
                        'currency': 'USD'
                    },
                    'customsValue': {
                        'amount': customs_amount_str,
                        'currency': 'USD'
                    },
                    'countryOfManufacture': getattr(settings, 'SHOP_COUNTRY', 'BG'),
                    'harmonizedCode': '7117190000'
                })
            
            customs_detail = {
                'dutiesPayment': {
                    'paymentType': 'SENDER'
                },
                'customsValue': {
                    'amount': customs_amount_str,
                    'currency': 'USD'
                },
                'totalCustomsValue': {
                    'amount': customs_amount_str,
                    'currency': 'USD'
                },
                'commodities': commodities  # REQUIRED by FedEx
            }
            requested_shipment['customsClearanceDetail'] = customs_detail
            import json
            logger.info(f"Added customs clearance detail with {len(commodities)} commodities (amount: {customs_amount_str} USD)")
        
        # Build shipment data structure
        shipment_data = {
            'labelResponseOptions': 'URL_ONLY',
            'requestedShipment': requested_shipment
        }
        
        # Add account number (REQUIRED by FedEx API)
        # Note: Account number must be authorized for use with these API credentials
        if self.account_number:
            # Ensure account number is properly formatted (as string, but ensure it's numeric)
            account_value = str(self.account_number).strip()
            # Verify it's numeric
            if not account_value.isdigit():
                logger.error(f"FedEx account number must be numeric, got: {account_value}")
                return None
            
            shipment_data['accountNumber'] = {
                'value': account_value
            }
            logger.info(f"Added accountNumber to shipment data: {account_value}")
            logger.info(f"Account number type: {type(account_value)}, value: '{account_value}'")
            
            # Add shipping location if configured (may be required for some accounts)
            shipping_location = getattr(settings, 'FEDEX_SHIPPING_LOCATION', None)
            logger.info(f"FEDEX_SHIPPING_LOCATION from settings: {shipping_location}")
            if shipping_location:
                if 'payor' not in shipment_data['requestedShipment']['shippingChargesPayment']:
                    shipment_data['requestedShipment']['shippingChargesPayment']['payor'] = {}
                if 'responsibleParty' not in shipment_data['requestedShipment']['shippingChargesPayment']['payor']:
                    shipment_data['requestedShipment']['shippingChargesPayment']['payor']['responsibleParty'] = {}
                
                shipment_data['requestedShipment']['shippingChargesPayment']['payor']['responsibleParty']['accountNumber'] = {
                    'value': account_value
                }
                shipment_data['requestedShipment']['shippingChargesPayment']['payor']['responsibleParty']['address'] = {
                    'countryCode': shipping_location
                }
                logger.info(f"✅ Added shipping location: {shipping_location} to payor.responsibleParty")
            else:
                logger.warning("⚠️ FEDEX_SHIPPING_LOCATION not set - may be required for account authorization")
        else:
            logger.error("FedEx account_number is REQUIRED but not provided!")
        
        logger.info(f"Final shipment data keys: {list(shipment_data.keys())}")
        if 'accountNumber' in shipment_data:
            logger.info(f"Account number in shipment: {shipment_data['accountNumber']}")
        
        return shipment_data
        
        # Add account number (REQUIRED by FedEx API)
        # Note: Account number must be authorized for use with these API credentials
        if self.account_number:
            # Ensure account number is properly formatted (as string, but ensure it's numeric)
            account_value = str(self.account_number).strip()
            # Verify it's numeric
            if not account_value.isdigit():
                logger.error(f"FedEx account number must be numeric, got: {account_value}")
                return None
            
            shipment_data['accountNumber'] = {
                'value': account_value
            }
            logger.info(f"Added accountNumber to shipment data: {account_value}")
            logger.info(f"Account number type: {type(account_value)}, value: '{account_value}'")
            
            # Add meter number if available (required for some operations)
            if self.meter_number:
                meter_value = str(self.meter_number).strip()
                # Meter number goes in the shipper contact or as separate field
                # For Ship API v1, meter number might be needed in shipper details
                if 'shipper' in shipment_data['requestedShipment']:
                    shipment_data['requestedShipment']['shipper']['tins'] = [{
                        'number': meter_value,
                        'tinType': 'BUSINESS_NATIONAL'
                    }]
                logger.info(f"Added meter number: {meter_value}")
            else:
                logger.warning("FedEx meter_number not provided - may be required for some operations")
        else:
            logger.error("FedEx account_number is REQUIRED but not provided!")
        
        logger.info(f"Final shipment data keys: {list(shipment_data.keys())}")
        if 'accountNumber' in shipment_data:
            logger.info(f"Account number in shipment: {shipment_data['accountNumber']}")
        
        return shipment_data


class DHLShipping(ShippingCarrierBase):
    """DHL Express shipping integration."""
    
    def __init__(self):
        super().__init__()
        self.api_key = getattr(settings, 'DHL_API_KEY', '')
        self.api_secret = getattr(settings, 'DHL_API_SECRET', '')
        self.account_number = getattr(settings, 'DHL_ACCOUNT_NUMBER', '')
        # MyDHL API (DHL Express) endpoint
        # Test: https://express.api.dhl.com/mydhlapi/test
        # Production: https://express.api.dhl.com/mydhlapi
        api_url = getattr(settings, 'DHL_API_URL', '')
        if not api_url or 'test' in api_url.lower() or 'sandbox' in api_url.lower():
            # MyDHL API test environment endpoint for shipments
            api_url = 'https://express.api.dhl.com/mydhlapi/test/shipments'
        elif 'express.api.dhl.com' not in api_url.lower():
            # Production MyDHL API endpoint
            api_url = 'https://express.api.dhl.com/mydhlapi/shipments'
        self.api_url = api_url
    
    def create_shipment(self, order) -> Optional[Dict[str, Any]]:
        """Create DHL shipment and return tracking info."""
        logger.info(f"DHL create_shipment called for Order #{order.id}")
        
        if not all([self.api_key, self.api_secret]):
            logger.error("DHL credentials not configured - missing API key or secret")
            return None
        
        # Account number is optional - can be derived from API or set later
        if not self.account_number:
            logger.warning("DHL account number not provided - will attempt to use userId or skip")
        
        try:
            # MyDHL API uses Basic Authentication (not OAuth)
            # Username = Site ID (consumerKey), Password = Password (consumerSecret)
            logger.info("Using Basic Auth for MyDHL API")
            logger.info(f"API Key (first 10 chars): {self.api_key[:10] if self.api_key else 'None'}...")
            
            # Prepare shipment data
            logger.info("Preparing DHL shipment data...")
            shipment_data = self._prepare_shipment_data(order)
            
            # Create shipment via MyDHL API using Basic Auth
            # MyDHL API uses Basic Auth: Authorization: Basic base64(username:password)
            # Username = Site ID (consumerKey), Password = Password (consumerSecret)
            auth = (self.api_key, self.api_secret)  # Username = Site ID, Password = Password
            headers = {
                'Content-Type': 'application/json',
                'Accept': 'application/json',
            }
            
            logger.info(f"Posting to MyDHL API: {self.api_url}")
            logger.info(f"Using Basic Auth with Username (Site ID): {self.api_key[:10]}...")
            logger.info(f"Account number in shipment: {shipment_data.get('accounts', [{}])[0].get('number', 'N/A')}")
            
            response = requests.post(
                self.api_url,
                json=shipment_data,
                headers=headers,
                auth=auth,  # Basic Auth with Site ID and Password
                timeout=30
            )
            
            logger.info(f"DHL API response status: {response.status_code}")
            logger.info(f"Response headers: {dict(response.headers)}")
            
            if response.status_code in [200, 201]:
                data = response.json()
                logger.info(f"DHL API response keys: {list(data.keys())}")
                
                # Extract tracking number and label
                tracking_number = data.get('shipmentTrackingNumber', '') or data.get('trackingNumber', '')
                label_data = data.get('label', {})
                
                # Label can be in different formats: b64Content, url, or documents array
                label_url = ''
                if isinstance(label_data, dict):
                    label_url = label_data.get('url', '') or label_data.get('b64Content', '')
                elif isinstance(label_data, list) and len(label_data) > 0:
                    label_url = label_data[0].get('url', '') or label_data[0].get('b64Content', '')
                
                # Check documents array if label not found
                documents = data.get('documents', [])
                if not label_url and documents:
                    for doc in documents:
                        if doc.get('typeCode') == 'label':
                            label_url = doc.get('url', '') or doc.get('content', '')
                            break
                
                logger.info(f"✅ DHL shipment created: tracking={tracking_number}, label_url={'present' if label_url else 'N/A'}")
                
                return {
                    'tracking_number': tracking_number or None,
                    'label_url': label_url or None,
                    'shipment_id': tracking_number or None,
                }
            else:
                logger.error(f"DHL API error: {response.status_code}")
                logger.error(f"DHL API response text (first 2000 chars): {response.text[:2000]}")
                try:
                    error_data = response.json()
                    logger.error(f"DHL API error response (JSON): {error_data}")
                except:
                    pass
                return None
                
        except Exception as e:
            logger.error(f"DHL shipment creation exception: {e}", exc_info=True)
            return None
    
    # Note: MyDHL API uses Basic Auth directly, not OAuth token
    # Authentication is done via Basic Auth header: Authorization: Basic base64(username:password)
    # Username = Site ID (consumerKey), Password = Password (consumerSecret)
    
    def _normalize_country_code(self, country: Optional[str]) -> str:
        """Normalize country code to ISO 2-letter format for DHL."""
        if not country:
            return 'BG'
        
        country = country.strip().upper()
        
        if len(country) == 2:
            return country
        
        country_mapping = {
            'BULGARIA': 'BG',
            'БЪЛГАРИЯ': 'BG',
            'UNITED STATES': 'US',
            'USA': 'US',
            'UNITED KINGDOM': 'GB',
            'UK': 'GB',
            'GERMANY': 'DE',
            'FRANCE': 'FR',
            'ITALY': 'IT',
            'SPAIN': 'ES',
            'GREECE': 'GR',
            'ROMANIA': 'RO',
            'TURKEY': 'TR',
        }
        
        if country in country_mapping:
            return country_mapping[country]
        
        logger.warning(f"Unknown country format: {country}, defaulting to BG")
        return 'BG'
    
    def _prepare_shipment_data(self, order) -> Dict[str, Any]:
        """Prepare shipment data for MyDHL API."""
        total_weight = sum(item.quantity for item in order.items.all()) * 0.5
        recipient_country = self._normalize_country_code(order.country)
        
        # MyDHL API requires account number in the shipment data
        # If not provided, use userId or try without it
        account_number = self.account_number or getattr(settings, 'DHL_ACCOUNT_NUMBER', '')
        
        # Determine product code based on destination
        # MyDHL API product codes: 'N' = Express Domestic, 'P' = Express Worldwide, 'U' = Express 12:00
        product_code = 'P'  # Express Worldwide for international
        
        # MyDHL API shipment data structure
        shipment_data = {
            'plannedShippingDateAndTime': order.created_at.strftime('%Y-%m-%dT%H:%M:%S'),
            'pickup': {
                'isRequested': False
            },
            'productCode': product_code,
            'accounts': [{
                'typeCode': 'shipper',
                'number': account_number if account_number else '123456789'  # Default test account if not provided
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
                        'countryCode': recipient_country,
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
        
        logger.info(f"Prepared shipment data with account number: {account_number or 'default'}")
        logger.debug(f"Shipment data structure: {shipment_data}")
        
        return shipment_data


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


class GlobalMailShipping(ShippingCarrierBase):
    """
    Deutsche Post International (DPI) / "Global Mail" shipping integration.

    Official spec (developer.dhl.com, GMPP API v5.7.10):
      - Sandbox: https://api-sandbox.dhl.com
      - Production: https://api.dhl.com
      - Auth: GET /dpi/v1/auth/accesstoken  with HTTP Basic(consumerKey:consumerSecret)
      - Shipping: /dpi/shipping/v1/orders, /items/{itemId}/label

    In test mode we only hit the sandbox environment, so no real/billable
    shipments are created. Switch GLOBAL_MAIL_TEST_MODE=False when ready for prod.
    """

    _token_cache: Dict[str, Any] = {}

    def __init__(self, force_sandbox: bool = False):
        super().__init__()
        self.consumer_key = getattr(settings, 'GLOBAL_MAIL_API_KEY', '')
        self.consumer_secret = getattr(settings, 'GLOBAL_MAIL_API_SECRET', '')
        self.user_id = getattr(settings, 'GLOBAL_MAIL_ACCOUNT_NUMBER', '')  # email userId (informational)
        self.customer_ekp = getattr(settings, 'GLOBAL_MAIL_CUSTOMER_EKP', '')
        settings_test = bool(getattr(settings, 'GLOBAL_MAIL_TEST_MODE', True))
        self.test_mode = True if force_sandbox else settings_test
        # Allow custom host override via GLOBAL_MAIL_API_URL. We accept either
        # a base host like "https://api.dhl.com" or a full URL containing
        # "/dpi/" — in either case we extract just the host portion.
        custom_url = (getattr(settings, 'GLOBAL_MAIL_API_URL', '') or '').strip()
        host = ''
        if custom_url and 'dpi' in custom_url.lower():
            # Extract scheme + host (drop /dpi/... path)
            try:
                from urllib.parse import urlparse
                p = urlparse(custom_url)
                if p.scheme and p.netloc:
                    host = f"{p.scheme}://{p.netloc}"
            except Exception:
                host = ''
        if not host:
            host = 'https://api-sandbox.dhl.com' if self.test_mode else 'https://api.dhl.com'
        self.host = host
        self.auth_url = f'{self.host}/dpi/v1/auth/accesstoken'
        self.orders_url = f'{self.host}/dpi/shipping/v1/orders'
        self.item_label_url = f'{self.host}/dpi/shipping/v1/items'  # append /{itemId}/label
        self.shipments_url = f'{self.host}/dpi/shipping/v1/shipments'  # append /{awb}/awblabels or /itemlabels
        logger.info(
            f"DPI Global Mail initialized · mode={'TEST (sandbox)' if self.test_mode else 'PRODUCTION'} · host={self.host}"
        )

    # ------------------------------------------------------------------
    # Label format helpers
    # ------------------------------------------------------------------
    @staticmethod
    def refit_pdf_to_4x6(pdf_bytes: bytes, margin_pt: float = 6.0) -> bytes:
        """Scale-fit the first page of a PDF onto a 4x6 inch page.

        Used for Zebra ZP-505 and similar thermal printers that cut off
        content near the bottom/edge if the PDF is generated at A5 or A6.
        Gracefully returns the original bytes if pypdf is not installed
        or the transformation fails.
        """
        try:
            from io import BytesIO
            from pypdf import PdfReader, PdfWriter, Transformation
            from pypdf.generic import RectangleObject

            TARGET_W = 288.0  # 4 inch * 72 pt/inch
            TARGET_H = 432.0  # 6 inch * 72 pt/inch

            reader = PdfReader(BytesIO(pdf_bytes))
            if not reader.pages:
                return pdf_bytes
            src = reader.pages[0]
            mb = src.mediabox
            src_w = float(mb.width)
            src_h = float(mb.height)
            if src_w <= 0 or src_h <= 0:
                return pdf_bytes

            inner_w = max(TARGET_W - 2 * margin_pt, 1.0)
            inner_h = max(TARGET_H - 2 * margin_pt, 1.0)
            scale = min(inner_w / src_w, inner_h / src_h)
            tx = (TARGET_W - src_w * scale) / 2.0
            ty = (TARGET_H - src_h * scale) / 2.0

            writer = PdfWriter()
            page = writer.add_blank_page(width=TARGET_W, height=TARGET_H)
            op = Transformation().scale(scale, scale).translate(tx, ty)
            page.merge_transformed_page(src, op)
            page.mediabox = RectangleObject((0, 0, TARGET_W, TARGET_H))
            page.cropbox = RectangleObject((0, 0, TARGET_W, TARGET_H))
            page.trimbox = RectangleObject((0, 0, TARGET_W, TARGET_H))
            out = BytesIO()
            writer.write(out)
            return out.getvalue()
        except Exception as exc:
            logger.warning(f"refit_pdf_to_4x6 failed, returning original PDF: {exc}")
            return pdf_bytes

    def _label_params(self) -> Dict[str, str]:
        """Query params appended to the /items/{id}/label request.

        Controls the size of the returned PDF label. Defaults to 4x6 (100 x
        150 mm) which matches thermal printers like the Zebra ZP-505. Can be
        overridden / disabled via env vars:
          - GLOBAL_MAIL_LABEL_PAGE_SIZE  (default: "4x6"; set to ""/"none" to disable)
          - GLOBAL_MAIL_LABEL_FORMAT     (default: "PDF")
        """
        params: Dict[str, str] = {}
        size = (getattr(settings, 'GLOBAL_MAIL_LABEL_PAGE_SIZE', '4x6') or '').strip()
        if size and size.lower() not in ('none', 'off', 'default'):
            params['pageSize'] = size
        fmt = (getattr(settings, 'GLOBAL_MAIL_LABEL_FORMAT', '') or '').strip()
        if fmt:
            params['format'] = fmt
        return params

    # ------------------------------------------------------------------
    # AWB (Airwaybill) transportation document
    # ------------------------------------------------------------------
    def get_awb_label(self, awb_number: str) -> Optional[bytes]:
        """Fetch the AWB (transportation document) PDF for a given AWB number.

        The AWB is a master dispatch document that bundles all items shipped
        under the same product+serviceLevel combination into one shipment.
        Required by DPI during certification and every pickup.

        Endpoint: GET /dpi/shipping/v1/shipments/{awb}/awblabels
        Returns raw PDF bytes on success, None on failure.
        """
        if not awb_number:
            logger.error("get_awb_label called without an AWB number")
            return None
        token = self._get_access_token()
        if not token:
            logger.error("DPI: cannot fetch AWB label without access token")
            return None
        url = f"{self.shipments_url}/{awb_number}/awblabels"
        try:
            r = requests.get(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/pdf",
                },
                timeout=30,
            )
        except Exception as exc:
            logger.error(f"DPI AWB label fetch exception: {exc}")
            return None
        if r.status_code != 200 or not r.content:
            logger.error(
                f"DPI AWB label HTTP {r.status_code}: {(r.text or '')[:400]}"
            )
            return None
        logger.info(f"DPI: fetched AWB label for {awb_number} ({len(r.content)} bytes)")
        return r.content

    # ------------------------------------------------------------------
    # OAuth 2.0 access token (cached 4h to stay under the 5h expiry)
    # ------------------------------------------------------------------
    def _get_access_token(self) -> Optional[str]:
        import time
        cache_key = f"{self.host}|{self.consumer_key}"
        cached = GlobalMailShipping._token_cache.get(cache_key)
        now = time.time()
        if cached and cached.get('expires_at', 0) > now + 60:
            return cached['token']

        if not (self.consumer_key and self.consumer_secret):
            logger.error("DPI Global Mail: consumerKey/consumerSecret not configured")
            return None

        try:
            logger.info(f"DPI Global Mail: requesting access token from {self.auth_url}")
            r = requests.get(
                self.auth_url,
                auth=(self.consumer_key, self.consumer_secret),
                headers={'Accept': 'application/json'},
                timeout=15,
            )
            if r.status_code != 200:
                logger.error(f"DPI token error {r.status_code}: {r.text[:300]}")
                return None
            data = r.json()
            token = data.get('access_token') or data.get('accessToken')
            if not token:
                logger.error(f"DPI token missing in response: {data}")
                return None
            ttl = int(data.get('expires_in') or 14400)
            GlobalMailShipping._token_cache[cache_key] = {
                'token': token,
                'expires_at': now + min(ttl, 14400),
            }
            logger.info(f"✅ DPI access_token cached (ttl={ttl}s)")
            return token
        except Exception as exc:
            logger.error(f"DPI token exception: {exc}", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Public entrypoint: create shipment + fetch PDF label
    # ------------------------------------------------------------------
    def create_shipment(self, order) -> Optional[Dict[str, Any]]:
        logger.info(f"DPI Global Mail: create_shipment for Order #{order.id}")

        if not self.customer_ekp:
            logger.error(
                "DPI Global Mail: GLOBAL_MAIL_CUSTOMER_EKP not set. "
                "Ask your DHL representative for your 10-digit EKP customer number."
            )
            return None

        token = self._get_access_token()
        if not token:
            return None

        try:
            payload = self._prepare_shipment_data(order)
            headers = {
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
                'Accept': 'application/json',
            }
            logger.info(f"DPI: POST {self.orders_url} (order #{order.id})")
            r = requests.post(self.orders_url, json=payload, headers=headers, timeout=30)
            if r.status_code not in (200, 201):
                logger.error(f"DPI create order {r.status_code}: {r.text[:1200]}")
                return None

            data = r.json() or {}
            # Extract first item id and AWB from shipments[0].items[0]
            shipments = data.get('shipments') or []
            item_id = None
            awb = None
            tracking = None
            if shipments:
                awb = shipments[0].get('awb') or shipments[0].get('awbNumber')
                items = shipments[0].get('items') or []
                if items:
                    first_item = items[0]
                    item_id = first_item.get('id') or first_item.get('itemId')
                    tracking = first_item.get('barcode') or awb

            if not item_id:
                # Some responses put items at top level
                items = data.get('items') or []
                if items:
                    item_id = items[0].get('id') or items[0].get('itemId')
                    tracking = items[0].get('barcode') or tracking

            # Fetch PDF label for the item (Accept: application/pdf → binary)
            label_b64 = None
            if item_id:
                label_url = f'{self.item_label_url}/{item_id}/label'
                label_params = self._label_params()
                logger.info(f"DPI: GET {label_url} params={label_params} for label PDF")
                lr = requests.get(
                    label_url,
                    headers={'Authorization': f'Bearer {token}', 'Accept': 'application/pdf'},
                    params=label_params or None,
                    timeout=30,
                )
                if lr.status_code == 200 and lr.content:
                    import base64
                    pdf_bytes = lr.content
                    if (getattr(settings, 'GLOBAL_MAIL_LABEL_FORCE_4X6', 'True') or '').lower() in ('1', 'true', 'yes', 'y', 'on'):
                        pdf_bytes = GlobalMailShipping.refit_pdf_to_4x6(pdf_bytes)
                    label_b64 = 'data:application/pdf;base64,' + base64.b64encode(pdf_bytes).decode('ascii')
                    logger.info(f"✅ DPI label PDF fetched ({len(pdf_bytes)} bytes, 4x6 refit applied)")
                else:
                    logger.warning(f"DPI label fetch {lr.status_code}: {lr.text[:300]}")

            result = {
                'tracking_number': tracking or awb or (f"DPI-TEST-{order.id}" if self.test_mode else None),
                'label_url': label_b64,
                'shipment_id': str(item_id) if item_id else (awb or None),
                'order_id_dpi': data.get('orderId'),
                'test_mode': self.test_mode,
            }
            logger.info(f"✅ DPI shipment created: tracking={result['tracking_number']}")
            return result
        except Exception as exc:
            logger.error(f"DPI create_shipment exception: {exc}", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Country-code & payload helpers (DPI / Global Mail)
    # ------------------------------------------------------------------
    def _normalize_country_code(self, country):
        if not country:
            return 'BG'
        c = str(country).strip().upper()
        if len(c) == 2:
            return c
        mapping = {
            'BULGARIA': 'BG', 'БЪЛГАРИЯ': 'BG',
            'UNITED STATES': 'US', 'USA': 'US',
            'UNITED KINGDOM': 'GB', 'UK': 'GB',
            'GERMANY': 'DE', 'FRANCE': 'FR', 'ITALY': 'IT',
            'SPAIN': 'ES', 'GREECE': 'GR', 'ROMANIA': 'RO', 'TURKEY': 'TR',
        }
        return mapping.get(c, c[:2] if len(c) >= 2 else 'BG')

    def _prepare_shipment_data(self, order):
        """
        Build a DPI "Create Order" payload (Workflow 1 - FINALIZE).
        Uses product GPT (Packet Tracked, lightweight goods) by default.
        Product can be overridden via settings.GLOBAL_MAIL_PRODUCT_CODE.
        """
        total_weight_kg = max(sum(item.quantity for item in order.items.all()) * 0.5, 0.1)
        total_weight_g = int(total_weight_kg * 1000)
        dest = self._normalize_country_code(getattr(order, 'country', 'BG'))
        default_product = getattr(settings, 'GLOBAL_MAIL_PRODUCT_CODE', 'GPT')
        # Built-in fallback for common non-EU destinations where GPT isn't valid
        # in PRODUCTION. In the sandbox environment only GPT is typically
        # available for every destination, so we skip the non-EU mapping there.
        _BUILTIN_NON_EU_PRODUCT_MAP = {
            "US": "GPP", "CA": "GPP", "AU": "GPP", "NZ": "GPP",
            "JP": "GPP", "KR": "GPP", "SG": "GPP", "HK": "GPP",
            "CN": "GPP", "IN": "GPP", "BR": "GPP", "MX": "GPP",
            "AE": "GPP", "IL": "GPP", "ZA": "GPP", "TR": "GPP",
            "CH": "GPP", "NO": "GPP", "IS": "GPP",
            "GB": "GPP",  # Great Britain is post-Brexit non-EU
        }
        user_map = getattr(settings, 'GLOBAL_MAIL_PRODUCT_MAP', {}) or {}
        if self.test_mode:
            # Sandbox: only apply the user's explicit override, never the built-in.
            product_map = user_map
        else:
            product_map = {**_BUILTIN_NON_EU_PRODUCT_MAP, **user_map}
        product = product_map.get(dest) or product_map.get(dest.upper()) or default_product
        service_map = getattr(settings, 'GLOBAL_MAIL_SERVICE_MAP', {}) or {}
        default_service = getattr(settings, 'GLOBAL_MAIL_SERVICE_LEVEL', 'PRIORITY')
        service = service_map.get(dest) or service_map.get(dest.upper()) or default_service
        currency = getattr(order, 'currency', None) or 'EUR'

        def _sanitize_phone(raw: str) -> str:
            """DPI allows: digits, leading +, spaces, dot, hyphen, parenthesis.
            No letters (so no 'ext.'), no slashes. Max 25 chars."""
            import re as _re
            s = (raw or '').strip()
            if not s:
                return ''
            s = _re.split(
                r'(?i)(?:\s*(?:ext\.?|extension)\b|(?<=\d)\s*x\s*(?=\d)|\bx\b)',
                s,
                maxsplit=1,
            )[0]
            has_plus = s.lstrip().startswith('+')
            s = _re.sub(r'[^\d\s\.\-\(\)]', '', s)
            if has_plus:
                s = '+' + s.lstrip()
            return s.strip()[:25]

        def _short_desc(raw: str) -> str:
            s = (raw or '').strip().replace('"', '').replace("'", '')
            if len(s) <= 33:
                return s if len(s) >= 3 else (s + ' item')[:33]
            # Keep it readable: prefer cutting at a word boundary.
            cut = s[:33]
            if ' ' in cut[-10:]:
                cut = cut.rsplit(' ', 1)[0]
            return cut[:33].strip() or 'Silver jewellery'

        def _unit_price(item) -> float:
            """Best-effort unit price for customs (variant → product discount → product → 0)."""
            variant = getattr(item, 'variant', None)
            if variant is not None:
                v = getattr(variant, 'price', None)
                if v:
                    try:
                        return float(v)
                    except Exception:
                        pass
            prod = getattr(item, 'product', None)
            if prod is not None:
                dp = getattr(prod, 'discount_price', None)
                if dp:
                    try:
                        return float(dp)
                    except Exception:
                        pass
                p = getattr(prod, 'price', None)
                if p:
                    try:
                        return float(p)
                    except Exception:
                        pass
            return 0.0

        order_total = 0.0
        try:
            order_total = float(getattr(order, 'total_price', 0) or 0)
        except Exception:
            order_total = 0.0
        total_qty = max(sum(int(it.quantity or 1) for it in order.items.all()), 1)
        fallback_unit = (order_total / total_qty) if order_total > 0 else 1.0

        contents = []
        running_index = 1
        total_amount = 0.0
        for it in order.items.all():
            name = getattr(it.product, 'name', '') or 'Silver jewellery'
            qty = int(it.quantity or 1)
            unit_price = _unit_price(it) or fallback_unit
            # DPI requires contentPieceValue >= 1
            unit_price = max(round(unit_price, 2), 1.0)
            total_amount += unit_price * qty
            contents.append({
                'contentPieceIndexNumber': running_index,
                'contentPieceAmount': qty,
                'contentPieceDescription': _short_desc(name),
                'contentPieceHsCode': getattr(settings, 'GLOBAL_MAIL_DEFAULT_HS_CODE', '711311'),
                'contentPieceOrigin': getattr(settings, 'SHOP_COUNTRY', 'BG'),
                'contentPieceValue': f"{unit_price:.2f}",
                'contentPieceNetweight': max(int((total_weight_g / max(len(order.items.all()), 1))), 10),
            })
            running_index += 1
        if not contents:
            contents = [{
                'contentPieceIndexNumber': 1,
                'contentPieceAmount': 1,
                'contentPieceDescription': 'Silver jewellery',
                'contentPieceHsCode': getattr(settings, 'GLOBAL_MAIL_DEFAULT_HS_CODE', '711311'),
                'contentPieceOrigin': getattr(settings, 'SHOP_COUNTRY', 'BG'),
                'contentPieceValue': f"{float(getattr(order, 'total', 0) or 0):.2f}",
                'contentPieceNetweight': total_weight_g,
            }]
            total_amount = float(getattr(order, 'total', 0) or 0)

        full_name = (getattr(order, 'full_name', '') or '')[:35]
        item = {
            'product': product,
            'serviceLevel': service,
            'recipient': full_name or 'Recipient',
            'recipientPhone': _sanitize_phone(getattr(order, 'phone', '') or ''),
            'recipientEmail': getattr(order, 'email', '') or '',
            'addressLine1': (getattr(order, 'address', '') or '')[:40] or 'Address',
            'city': getattr(order, 'city', '') or 'City',
            'postalCode': getattr(order, 'postal_code', '') or '0000',
            'destinationCountry': dest,
            'shipmentAmount': round(total_amount or 1.0, 2),
            'shipmentCurrency': currency,
            'shipmentGrossWeight': total_weight_g,
            'shipmentNaturetype': getattr(settings, 'GLOBAL_MAIL_NATURE_TYPE', 'SALE_GOODS'),
            'returnItemWanted': False,
            'custRef': str(order.id),
            'contents': contents,
        }

        payload = {
            'customerEkp': str(self.customer_ekp),
            'orderStatus': 'FINALIZE',
            'paperwork': {
                'contactName': (getattr(settings, 'SHOP_CONTACT_NAME', 'Marbaras'))[:35],
                'jobReference': f'Order-{order.id}'[:17],
                'telephoneNumber': (getattr(settings, 'SHOP_PHONE', '') or '+359888000000'),
                'awbCopyCount': 1,
            },
            'items': [item],
        }
        logger.debug(f"DPI payload: {payload}")
        return payload


class EasyPostShipping(ShippingCarrierBase):
    """EasyPost shipping integration - unified API for multiple carriers."""
    
    def __init__(self):
        super().__init__()
        self.api_key = getattr(settings, 'EASYPOST_API_KEY', '')
        if not easypost:
            logger.error("easypost library not installed. Install with: pip install easypost")
        elif self.api_key:
            easypost.api_key = self.api_key
    
    def create_shipment(self, order) -> Optional[Dict[str, Any]]:
        """Create EasyPost shipment and return tracking info."""
        logger.info(f"EasyPost create_shipment called for Order #{order.id}")
        
        if not easypost:
            logger.error("easypost library not installed")
            return None
        
        if not self.api_key:
            logger.error("EasyPost API key not configured")
            return None
        
        try:
            # Set API key
            easypost.api_key = self.api_key
            
            # Determine carrier from shipping_option or use default
            carrier = 'FedEx'  # Default carrier
            if order.shipping_option:
                option_name = order.shipping_option.name.lower()
                if 'dhl' in option_name:
                    carrier = 'DHLExpress'
                elif 'ups' in option_name:
                    carrier = 'UPS'
                elif 'usps' in option_name:
                    carrier = 'USPS'
                elif 'fedex' in option_name:
                    carrier = 'FedEx'
            
            # Create from address (shop address)
            shop_country = getattr(settings, 'SHOP_COUNTRY', 'BG')
            shop_state = getattr(settings, 'SHOP_STATE', '')
            
            from_address = easypost.Address.create(
                name=getattr(settings, 'SHOP_NAME', 'Marbaras'),
                street1=getattr(settings, 'SHOP_ADDRESS', ''),
                city=getattr(settings, 'SHOP_CITY', 'Sofia'),
                state=shop_state if shop_state else None,
                zip=getattr(settings, 'SHOP_POSTAL_CODE', ''),
                country=shop_country,
                phone=getattr(settings, 'SHOP_PHONE', ''),
            )
            
            # Create to address (customer address)
            to_address = easypost.Address.create(
                name=order.full_name,
                street1=order.address,
                city=order.city,
                state=None,  # EasyPost will handle this if needed
                zip=order.postal_code,
                country=order.country or 'BG',
                phone=order.phone,
            )
            
            # Calculate package weight and dimensions
            total_weight = Decimal('0.5')  # Default 0.5 kg
            for item in order.items.all():
                product_weight = getattr(item.product, 'weight', Decimal('0.1'))
                total_weight += product_weight * Decimal(str(item.quantity))
            
            # Create parcel
            parcel = easypost.Parcel.create(
                length=20,  # cm
                width=15,   # cm
                height=10,   # cm
                weight=float(total_weight),
            )
            
            # Create shipment
            logger.info(f"Creating EasyPost shipment with carrier: {carrier}")
            shipment = easypost.Shipment.create(
                to_address=to_address,
                from_address=from_address,
                parcel=parcel,
                carrier=carrier,
                service=None,  # Let EasyPost choose best service
            )
            
            # Buy the shipment (purchase label)
            logger.info("Purchasing EasyPost label...")
            shipment.buy(rate=shipment.lowest_rate())
            
            # Extract tracking and label info
            tracking_number = shipment.tracking_code
            label_url = shipment.postage_label.label_url if shipment.postage_label else None
            shipment_id = shipment.id
            
            logger.info(f"✅ EasyPost shipment created: tracking={tracking_number}, label_url={label_url}")
            
            return {
                'tracking_number': tracking_number,
                'label_url': label_url,
                'shipment_id': shipment_id,
            }
            
        except easypost.Error as e:
            logger.error(f"EasyPost API error: {e}", exc_info=True)
            return None
        except Exception as e:
            logger.error(f"EasyPost shipment creation exception: {e}", exc_info=True)
            return None


def get_shipping_carrier(carrier_name: str) -> Optional[ShippingCarrierBase]:
    """Factory function to get shipping carrier instance."""
    carriers = {
        'fedex': FedExShipping,
        'dhl': DHLShipping,
        'deutsche_post': DeutschePostShipping,
        'global_mail': GlobalMailShipping,
        'easypost': EasyPostShipping,
    }
    
    carrier_class = carriers.get(carrier_name.lower())
    if carrier_class:
        return carrier_class()
    return None


def detect_carrier_for_order(order) -> Optional[str]:
    """
    Determine the carrier key ('fedex', 'global_mail', ...) for an order.

    Priority:
      1. order.shipping_carrier if it is set to a known carrier key.
      2. order.shipping_option.name (case-insensitive) matched against
         known carrier keywords.
    Returns None if no carrier can be confidently determined.
    """
    valid_keys = {'fedex', 'dhl', 'deutsche_post', 'global_mail', 'easypost'}

    explicit = (getattr(order, 'shipping_carrier', '') or '').strip().lower()
    if explicit in valid_keys:
        return explicit

    option = getattr(order, 'shipping_option', None)
    option_name = (getattr(option, 'name', '') or '').lower()
    if not option_name:
        return None

    if 'easypost' in option_name:
        return 'easypost'
    if 'fedex' in option_name:
        return 'fedex'
    # Global Mail / Global Post must match before the generic 'dhl' branch
    # because Global Mail rides on the MyDHL API.
    if 'global' in option_name and ('mail' in option_name or 'post' in option_name):
        return 'global_mail'
    if 'dhl' in option_name:
        return 'dhl'
    if 'deutsche' in option_name:
        return 'deutsche_post'
    return None


def create_shipping_label(order, carrier_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Create shipping label for an order.
    If carrier_name is not provided, tries to determine from shipping_option.
    """
    if not carrier_name:
        carrier_name = detect_carrier_for_order(order)

    if not carrier_name:
        logger.warning(f"No carrier specified for order {order.id}")
        return None

    carrier = get_shipping_carrier(carrier_name)
    if not carrier:
        logger.error(f"Unknown carrier: {carrier_name}")
        return None

    return carrier.create_shipment(order)


def auto_create_shipping_label(order_id: int) -> Optional[Dict[str, Any]]:
    """
    Safe, idempotent wrapper used for automatic label creation right after
    an order is placed.

    - Reloads the order by id so this can be called from a background thread
      without holding a stale ORM instance.
    - Skips if the order already has a shipping label (idempotent).
    - Detects the carrier (FedEx / Global Mail / ...) via
      :func:`detect_carrier_for_order`.
    - Persists ``tracking_number``, ``shipping_label_url``, ``shipment_id``
      and ``shipping_carrier`` on the order.
    - Catches and logs every exception so a failed label never breaks the
      customer's checkout flow.
    """
    try:
        from ecommerce.models import Order  # local import to avoid cycles
    except Exception:
        logger.exception("auto_create_shipping_label: failed to import Order model")
        return None

    try:
        order = Order.objects.select_related('shipping_option').get(pk=order_id)
    except Exception:
        logger.exception(
            "auto_create_shipping_label: order #%s could not be loaded", order_id
        )
        return None

    if order.shipping_label_url:
        logger.info(
            "auto_create_shipping_label: order #%s already has a label, skipping",
            order_id,
        )
        return {
            'tracking_number': order.tracking_number,
            'label_url': order.shipping_label_url,
            'shipment_id': order.shipment_id,
        }

    carrier_name = detect_carrier_for_order(order)
    if not carrier_name:
        logger.info(
            "auto_create_shipping_label: no carrier detected for order #%s "
            "(shipping_option=%r)",
            order_id,
            getattr(order.shipping_option, 'name', None),
        )
        return None

    logger.info(
        "auto_create_shipping_label: creating %s label for order #%s",
        carrier_name, order_id,
    )

    try:
        result = create_shipping_label(order, carrier_name)
    except Exception:
        logger.exception(
            "auto_create_shipping_label: carrier API raised for order #%s (%s)",
            order_id, carrier_name,
        )
        return None

    if not result:
        logger.warning(
            "auto_create_shipping_label: carrier returned no data for order #%s (%s)",
            order_id, carrier_name,
        )
        return None

    try:
        update_fields = []
        tracking = result.get('tracking_number')
        label_url = result.get('label_url')
        shipment_id = result.get('shipment_id')

        if tracking and tracking != order.tracking_number:
            order.tracking_number = tracking
            update_fields.append('tracking_number')
        if label_url and label_url != order.shipping_label_url:
            order.shipping_label_url = label_url
            update_fields.append('shipping_label_url')
        if shipment_id and shipment_id != order.shipment_id:
            order.shipment_id = shipment_id
            update_fields.append('shipment_id')
        if not order.shipping_carrier:
            order.shipping_carrier = carrier_name
            update_fields.append('shipping_carrier')

        if update_fields:
            order.save(update_fields=update_fields)
            logger.info(
                "auto_create_shipping_label: order #%s updated with fields=%s "
                "(tracking=%s)",
                order_id, update_fields, order.tracking_number,
            )
    except Exception:
        logger.exception(
            "auto_create_shipping_label: failed to persist label data for order #%s",
            order_id,
        )
        return None

    return result

