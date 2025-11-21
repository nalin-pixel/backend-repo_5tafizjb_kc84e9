import os
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import requests

from supabase_client import get_supabase

app = FastAPI(title="QuickCommerce API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------------
# Models
# -----------------------------
class Product(BaseModel):
    id: str
    name: str
    sku: Optional[str] = None
    category: Optional[str] = None
    price: float
    image_url: Optional[str] = None
    stock: int = 0
    seller_id: Optional[str] = None


class CartItem(BaseModel):
    product_id: str
    quantity: int = Field(ge=1)


class CreateOrderRequest(BaseModel):
    user_id: Optional[str] = None
    address: str
    coordinates: Optional[List[float]] = Field(default=None, description="[lon, lat]")
    items: List[CartItem]
    payment_method: str = Field(default="COD")
    delivery_window_minutes: int = Field(default=20)


class RiderLocation(BaseModel):
    rider_id: str
    order_id: Optional[str] = None
    lon: float
    lat: float
    speed: Optional[float] = None
    heading: Optional[float] = None


# -----------------------------
# Helpers
# -----------------------------

def demo_mode() -> bool:
    # If Supabase is not configured, we operate in demo mode
    return get_supabase() is None


def ensure_supabase():
    sb = get_supabase()
    if sb is None:
        raise HTTPException(status_code=501, detail="Supabase not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in environment.")
    return sb


# -----------------------------
# Health
# -----------------------------
@app.get("/")
def root():
    return {"service": "QuickCommerce API", "status": "ok"}


@app.get("/test")
def test_env():
    return {
        "backend": "✅ Running",
        "supabase": "✅ Configured" if not demo_mode() else "❌ Not Configured (demo mode)",
        "SUPABASE_URL": bool(os.getenv("SUPABASE_URL")),
    }


# -----------------------------
# Products & Inventory
# -----------------------------
@app.get("/api/products", response_model=List[Product])
def list_products(q: Optional[str] = None, category: Optional[str] = None, limit: int = 50):
    if demo_mode():
        sample = [
            {"id": "1", "name": "Bananas", "sku": "BN-1", "category": "Fruits", "price": 0.99, "image_url": "https://images.unsplash.com/photo-1571772805064-207c8435df79?w=400", "stock": 120},
            {"id": "2", "name": "Milk 1L", "sku": "ML-1L", "category": "Dairy", "price": 1.49, "image_url": "https://images.unsplash.com/photo-1580910051074-3eb694886505?w=400", "stock": 32},
            {"id": "3", "name": "Brown Bread", "sku": "BR-01", "category": "Bakery", "price": 1.99, "image_url": "https://images.unsplash.com/photo-1608198093002-ad4e005484ec?w=400", "stock": 8},
        ]
        # filter
        def match(p: Dict[str, Any]) -> bool:
            ok = True
            if q:
                ok = ok and (q.lower() in p["name"].lower())
            if category:
                ok = ok and (p.get("category") == category)
            return ok
        return [Product(**p) for p in sample if match(p)][:limit]

    sb = ensure_supabase()
    query = sb.table("products").select("*")
    if q:
        query = query.ilike("name", f"%{q}%")
    if category:
        query = query.eq("category", category)
    data = query.limit(limit).execute().data or []
    # Map stock from inventory view if present
    items: List[Product] = []
    for row in data:
        items.append(Product(
            id=str(row.get("id")),
            name=row.get("name") or row.get("title") or "Item",
            sku=row.get("sku"),
            category=row.get("category"),
            price=float(row.get("price") or 0),
            image_url=row.get("image_url"),
            stock=int(row.get("stock") or row.get("quantity") or 0),
            seller_id=row.get("seller_id"),
        ))
    return items


@app.post("/api/inventory/update")
def update_inventory(product_id: str, delta: int):
    if demo_mode():
        return {"status": "ok", "demo": True}
    sb = ensure_supabase()
    # Assumes "products" has a numeric stock column
    res = sb.rpc("adjust_stock", {"p_id": product_id, "delta": delta}).execute()
    return {"status": "ok", "result": res.data}


# -----------------------------
# Cart & Orders
# -----------------------------
@app.post("/api/orders")
def create_order(payload: CreateOrderRequest):
    if demo_mode():
        return {
            "status": "ok",
            "order_id": "demo-123",
            "eta_minutes": max(10, min(20, payload.delivery_window_minutes)),
            "tracking": {"status": "PENDING", "rider": None},
        }
    sb = ensure_supabase()
    order_row = {
        "user_id": payload.user_id,
        "address": payload.address,
        "coordinates": payload.coordinates,
        "payment_method": payload.payment_method,
        "status": "PENDING",
    }
    order = sb.table("orders").insert(order_row).select("id").single().execute().data
    order_id = order["id"]
    # insert items
    items = [
        {"order_id": order_id, "product_id": it.product_id, "quantity": it.quantity}
        for it in payload.items
    ]
    if items:
        sb.table("order_items").insert(items).execute()
    return {"status": "ok", "order_id": order_id}


@app.get("/api/orders/{order_id}")
def get_order(order_id: str):
    if demo_mode():
        return {"order_id": order_id, "status": "OUT_FOR_DELIVERY", "eta_minutes": 12}
    sb = ensure_supabase()
    order = sb.table("orders").select("*").eq("id", order_id).single().execute().data
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


# -----------------------------
# Routing with OSRM
# -----------------------------
@app.get("/api/route")
def route(start: str = Query(..., description="lon,lat"), end: str = Query(..., description="lon,lat")):
    # Use OSRM public server
    osrm_url = f"https://router.project-osrm.org/route/v1/driving/{start};{end}?overview=full&geometries=geojson"
    r = requests.get(osrm_url, timeout=10)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="OSRM error")
    data = r.json()
    return data


# -----------------------------
# Rider location (store in Supabase table 'rider_locations')
# -----------------------------
@app.post("/api/rider/location")
def upsert_rider_location(loc: RiderLocation):
    if demo_mode():
        return {"status": "ok", "demo": True}
    sb = ensure_supabase()
    row = loc.model_dump()
    res = sb.table("rider_locations").upsert(row, on_conflict="rider_id").execute().data
    return {"status": "ok", "data": res}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
