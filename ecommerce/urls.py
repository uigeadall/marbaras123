from django.urls import path
from . import views



urlpatterns = [
    path('', views.home, name='home'),
    path("reviews/", views.customer_reviews_list, name="customer_reviews"),
    path('register/', views.register_view, name='register'),
    path('logout/', views.logout_view, name='logout'),
    path('favorites/', views.favorites_list, name='favorites_list'),
    path('favorites/remove/<int:pk>/', views.remove_from_favorites, name='remove_from_favorites'),
    path('cart/', views.cart_view, name='cart_view'),
    path('cart/remove/<int:pk>/', views.remove_from_cart, name='remove_from_cart'),
    path('products/', views.product_list, name='product_list'),
    path('product/<slug:slug>/', views.product_detail, name='product_detail'),
    path('cart/add/<int:pk>/', views.add_to_cart, name='add_to_cart'),
    path('favorites/add/<int:pk>/', views.toggle_favorite, name='add_to_favorites'),
    path('checkout/', views.checkout_view, name='checkout'),
    path(
        'checkout/refresh-payment-intent/',
        views.checkout_refresh_payment_intent,
        name='checkout_refresh_payment_intent',
    ),
    # Category URLs - handle both old numeric slugs and new text slugs
    path('category/<slug:slug>/', views.products_by_category, name='products_by_category'),
    path('success/', views.payment_success, name='success'),
    path('webhook/', views.stripe_webhook, name='stripe_webhook'),
    path('stripe/webhook/', views.stripe_webhook, name='stripe_webhook_stripe_path'),
    path('toggle-favorite/<int:pk>/', views.toggle_favorite, name='toggle_favorite'),
    path('cart/update/<int:pk>/', views.update_cart_quantity, name='update_cart_quantity'),
    path('order-success/', views.order_success, name='order_success'),
path("terms/", views.terms, name="terms"),
    path("privacy/", views.privacy, name="privacy"),
    path("refund-returns/", views.refund_returns, name="refund_returns"),
    path("contact/", views.contact, name="contact"),
path("account/", views.profile_dashboard, name="profile_dashboard"),
    path("account/favorites/", views.profile_favorites, name="profile_favorites"),
    path("account/orders/", views.profile_orders, name="profile_orders"),
    path("account/details/", views.profile_details, name="profile_details"),


path('checkout/guest/', views.guest_checkout_view, name='guest_checkout'),


    path("login/", views.login_view, name="login"),
    path("blog/<slug:slug>/", views.blog_detail, name="blog_detail"),
    path("health/", views.health_check, name="health_check"),
    path("test-emails/", views.test_emails_view, name="test_emails"),
    path("create-order-from-product/", views.create_order_from_product, name="create_order_from_product"),
    path(
        "create-order-from-cart-wallet/",
        views.create_order_from_cart_wallet,
        name="create_order_from_cart_wallet",
    ),
    path("sitemap.xml", views.sitemap_xml, name="sitemap_xml"),
    path("robots.txt", views.robots_txt, name="robots_txt"),
    path("subscribe-email/", views.subscribe_email, name="subscribe_email"),
    path("validate-coupon/", views.validate_coupon, name="validate_coupon"),
]
















