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
        # Ensure API URL includes the full path
        api_url = getattr(settings, 'FEDEX_API_URL', 'https://apis.fedex.com/ship/v1/shipments')
        # If URL doesn't end with /ship/v1/shipments, add it
        if api_url and not api_url.endswith('/ship/v1/shipments'):
            if api_url.endswith('/'):
                api_url = api_url.rstrip('/')
            if 'sandbox' in api_url.lower():
                api_url = 'https://apis-sandbox.fedex.com/ship/v1/shipments'
            else:
                api_url = 'https://apis.fedex.com/ship/v1/shipments'
        self.api_url = api_url
    
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
                logger.error(f"FedEx API response text (first 2000 chars): {response.text[:2000]}")
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
            data = {
                'grant_type': 'client_credentials',
                'client_id': self.api_key,
                'client_secret': self.api_secret
            }
            response = requests.post(token_url, data=data, timeout=10)
            
            if response.status_code == 200:
                token = response.json().get('access_token')
                logger.info("✅ FedEx OAuth token obtained successfully")
                return token
            else:
                logger.error(f"FedEx token request failed: {response.status_code} - {response.text}")
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
        # MyDHL API uses: https://api-mydhl.dhl.com/ for production
        # Sandbox: https://api-sandbox.dhl.com/
        api_url = getattr(settings, 'DHL_API_URL', '')
        if not api_url or 'sandbox' in api_url.lower():
            # MyDHL API sandbox endpoint for shipments
            api_url = 'https://api-sandbox.dhl.com/mydhlapi/shipments'
        elif 'mydhl' not in api_url.lower():
            # Production MyDHL API endpoint
            api_url = 'https://api-mydhl.dhl.com/mydhlapi/shipments'
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
            # Get OAuth token
            logger.info("Requesting DHL OAuth token...")
            token = self._get_access_token()
            if not token:
                logger.error("Failed to obtain DHL OAuth token")
                return None
            logger.info("✅ DHL OAuth token obtained")
            
            # Prepare shipment data
            logger.info("Preparing DHL shipment data...")
            shipment_data = self._prepare_shipment_data(order)
            
            # Create shipment via DHL API
            headers = {
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
            }
            
            logger.info(f"Posting to DHL API: {self.api_url}")
            response = requests.post(
                self.api_url,
                json=shipment_data,
                headers=headers,
                timeout=30
            )
            
            logger.info(f"DHL API response status: {response.status_code}")
            
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
    
    def _get_access_token(self) -> Optional[str]:
        """Get OAuth access token from DHL."""
        try:
            # MyDHL API (DHL Express) authentication endpoint
            # MyDHL API uses Site ID (consumerKey) and Password (consumerSecret) for Basic Auth
            if 'sandbox' in self.api_url.lower():
                # MyDHL API sandbox authentication endpoint
                token_urls = [
                    'https://api-sandbox.dhl.com/auth/accesstoken',  # MyDHL API standard endpoint
                    'https://api-sandbox.dhl.com/mydhlapi/auth/accesstoken',  # Alternative format
                ]
            else:
                # MyDHL API production authentication endpoint
                token_urls = [
                    'https://api-mydhl.dhl.com/auth/accesstoken',  # MyDHL API standard endpoint
                    'https://api-mydhl.dhl.com/mydhlapi/auth/accesstoken',  # Alternative format
                ]
            
            # Try Basic Auth first (most common for DHL)
            # DHL Express API uses Site ID (consumerKey) as username and Password (consumerSecret) as password
            for token_url in token_urls:
                logger.info(f"Trying DHL OAuth token from {token_url}")
                logger.info(f"Using API Key (first 10 chars): {self.api_key[:10] if self.api_key else 'None'}...")
                logger.info(f"Using API Secret (first 10 chars): {self.api_secret[:10] if self.api_secret else 'None'}...")
                
                try:
                    # Method 1: Basic Auth with Site ID and Password
                    # DHL Express API expects: Authorization: Basic base64(SiteID:Password)
                    auth = (self.api_key, self.api_secret)
                    headers = {
                        'Content-Type': 'application/x-www-form-urlencoded',
                    }
                    response = requests.post(token_url, auth=auth, headers=headers, timeout=10)
                    
                    logger.info(f"Response status: {response.status_code}")
                    if response.status_code == 200:
                        token = response.json().get('access_token') or response.json().get('accessToken') or response.json().get('token')
                        if token:
                            logger.info(f"✅ DHL OAuth token obtained successfully from {token_url}")
                            return token
                    
                    # Method 2: Form data with client credentials (OAuth 2.0 standard)
                    if response.status_code == 401:
                        logger.info(f"Basic auth failed, trying OAuth 2.0 form data method...")
                        data = {
                            'grant_type': 'client_credentials',
                            'client_id': self.api_key,
                            'client_secret': self.api_secret
                        }
                        headers = {
                            'Content-Type': 'application/x-www-form-urlencoded',
                        }
                        response = requests.post(token_url, data=data, headers=headers, timeout=10)
                        
                        logger.info(f"Form data response status: {response.status_code}")
                        if response.status_code == 200:
                            token = response.json().get('access_token') or response.json().get('accessToken') or response.json().get('token')
                            if token:
                                logger.info(f"✅ DHL OAuth token obtained successfully (form data) from {token_url}")
                                return token
                    
                    # Method 3: Try with JSON body (some APIs use this)
                    if response.status_code == 401:
                        logger.info(f"Trying JSON body method...")
                        json_data = {
                            'grant_type': 'client_credentials',
                            'client_id': self.api_key,
                            'client_secret': self.api_secret
                        }
                        headers = {
                            'Content-Type': 'application/json',
                        }
                        response = requests.post(token_url, json=json_data, headers=headers, timeout=10)
                        
                        logger.info(f"JSON body response status: {response.status_code}")
                        if response.status_code == 200:
                            token = response.json().get('access_token') or response.json().get('accessToken') or response.json().get('token')
                            if token:
                                logger.info(f"✅ DHL OAuth token obtained successfully (JSON) from {token_url}")
                                return token
                    
                    # Log error for this endpoint
                    if response.status_code != 200:
                        logger.warning(f"DHL token request failed for {token_url}: {response.status_code}")
                        logger.warning(f"Response text: {response.text[:500]}")
                        logger.warning(f"Response headers: {dict(response.headers)}")
                        
                except Exception as e:
                    logger.warning(f"Exception trying {token_url}: {e}", exc_info=True)
                    continue
            
            # If all endpoints failed
            logger.error(f"All DHL token endpoints failed. Last error: {response.status_code} - {response.text[:500]}")
            return None
            
        except Exception as e:
            logger.error(f"DHL token request exception: {e}", exc_info=True)
            return None
    
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
        """Prepare shipment data for DHL API."""
        total_weight = sum(item.quantity for item in order.items.all()) * 0.5
        recipient_country = self._normalize_country_code(order.country)
        
        # Determine product code based on destination
        # 'N' = Express Domestic, 'P' = Express Worldwide, 'U' = Express 12:00
        product_code = 'P'  # Express Worldwide for international
        
        return {
            'plannedShippingDateAndTime': order.created_at.strftime('%Y-%m-%dT%H:%M:%S'),
            'pickup': {
                'isRequested': False
            },
            'productCode': product_code,
            'accounts': ([{
                'typeCode': 'shipper',
                'number': self.account_number
            }] if self.account_number else []),
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
    """Global Mail shipping integration."""
    
    def __init__(self):
        super().__init__()
        self.api_key = getattr(settings, 'GLOBAL_MAIL_API_KEY', '')
        self.api_secret = getattr(settings, 'GLOBAL_MAIL_API_SECRET', '')
        self.account_number = getattr(settings, 'GLOBAL_MAIL_ACCOUNT_NUMBER', '')
        api_url = getattr(settings, 'GLOBAL_MAIL_API_URL', 'https://api.globalmail.com/v1/shipments')
        if 'sandbox' in api_url.lower() or not api_url:
            api_url = 'https://api-sandbox.globalmail.com/v1/shipments'
        self.api_url = api_url
    
    def create_shipment(self, order) -> Optional[Dict[str, Any]]:
        """Create Global Mail shipment and return tracking info."""
        logger.info(f"Global Mail create_shipment called for Order #{order.id}")
        
        if not all([self.api_key, self.api_secret, self.account_number]):
            logger.error("Global Mail credentials not configured - missing API key, secret, or account number")
            return None
        
        try:
            # Get OAuth token
            logger.info("Requesting Global Mail OAuth token...")
            token = self._get_access_token()
            if not token:
                logger.error("Failed to obtain Global Mail OAuth token")
                return None
            logger.info("✅ Global Mail OAuth token obtained")
            
            # Prepare shipment data
            logger.info("Preparing Global Mail shipment data...")
            shipment_data = self._prepare_shipment_data(order)
            
            # Create shipment via Global Mail API
            headers = {
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
            }
            
            logger.info(f"Posting to Global Mail API: {self.api_url}")
            response = requests.post(
                self.api_url,
                json=shipment_data,
                headers=headers,
                timeout=30
            )
            
            logger.info(f"Global Mail API response status: {response.status_code}")
            
            if response.status_code in [200, 201]:
                data = response.json()
                logger.info(f"Global Mail API response keys: {list(data.keys())}")
                
                # Extract tracking number and label
                tracking_number = data.get('tracking_number', '') or data.get('trackingNumber', '') or data.get('shipment_id', '')
                label_url = data.get('label_url', '') or data.get('labelUrl', '') or data.get('label', {}).get('url', '')
                
                logger.info(f"✅ Global Mail shipment created: tracking={tracking_number}, label_url={'present' if label_url else 'N/A'}")
                
                return {
                    'tracking_number': tracking_number or None,
                    'label_url': label_url or None,
                    'shipment_id': tracking_number or None,
                }
            else:
                logger.error(f"Global Mail API error: {response.status_code}")
                logger.error(f"Global Mail API response text (first 2000 chars): {response.text[:2000]}")
                try:
                    error_data = response.json()
                    logger.error(f"Global Mail API error response (JSON): {error_data}")
                except:
                    pass
                return None
                
        except Exception as e:
            logger.error(f"Global Mail shipment creation exception: {e}", exc_info=True)
            return None
    
    def _get_access_token(self) -> Optional[str]:
        """Get OAuth access token from Global Mail."""
        try:
            token_url = 'https://api.globalmail.com/oauth/token'
            if 'sandbox' in self.api_url.lower():
                token_url = 'https://api-sandbox.globalmail.com/oauth/token'
            
            logger.info(f"Requesting Global Mail OAuth token from {token_url}")
            data = {
                'grant_type': 'client_credentials',
                'client_id': self.api_key,
                'client_secret': self.api_secret
            }
            response = requests.post(token_url, data=data, timeout=10)
            
            if response.status_code == 200:
                token = response.json().get('access_token') or response.json().get('accessToken')
                logger.info("✅ Global Mail OAuth token obtained successfully")
                return token
            else:
                logger.error(f"Global Mail token request failed: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            logger.error(f"Global Mail token request exception: {e}", exc_info=True)
            return None
    
    def _normalize_country_code(self, country: Optional[str]) -> str:
        """Normalize country code to ISO 2-letter format for Global Mail."""
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
        """Prepare shipment data for Global Mail API."""
        total_weight = sum(item.quantity for item in order.items.all()) * 0.5
        recipient_country = self._normalize_country_code(order.country)
        
        return {
            'account_number': self.account_number,
            'ship_date': order.created_at.strftime('%Y-%m-%d'),
            'shipper': {
                'name': 'Marbaras',
                'address': getattr(settings, 'SHOP_ADDRESS', ''),
                'city': getattr(settings, 'SHOP_CITY', 'Sofia'),
                'postal_code': getattr(settings, 'SHOP_POSTAL_CODE', ''),
                'country': getattr(settings, 'SHOP_COUNTRY', 'BG'),
                'phone': getattr(settings, 'SHOP_PHONE', ''),
                'email': getattr(settings, 'SHOP_EMAIL', ''),
            },
            'recipient': {
                'name': order.full_name,
                'address': order.address,
                'city': order.city,
                'postal_code': order.postal_code,
                'country': recipient_country,
                'phone': order.phone,
                'email': order.email or '',
            },
            'package': {
                'weight': max(total_weight, 0.5),
                'weight_unit': 'kg',
                'dimensions': {
                    'length': 20,
                    'width': 15,
                    'height': 10,
                    'unit': 'cm'
                },
                'value': float(order.total_price),
                'currency': 'USD'
            },
            'label_format': 'PDF',
            'label_size': '4x6'
        }


def get_shipping_carrier(carrier_name: str) -> Optional[ShippingCarrierBase]:
    """Factory function to get shipping carrier instance."""
    carriers = {
        'fedex': FedExShipping,
        'dhl': DHLShipping,
        'deutsche_post': DeutschePostShipping,
        'global_mail': GlobalMailShipping,
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
        elif 'global' in option_name and 'mail' in option_name:
            carrier_name = 'global_mail'
    
    if not carrier_name:
        logger.warning(f"No carrier specified for order {order.id}")
        return None
    
    carrier = get_shipping_carrier(carrier_name)
    if not carrier:
        logger.error(f"Unknown carrier: {carrier_name}")
        return None
    
    return carrier.create_shipment(order)

