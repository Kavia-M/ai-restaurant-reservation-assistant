from typing import List, Optional, Dict, Tuple, Any
from datetime import datetime, timedelta, timezone
from math import ceil, radians, cos, sin, asin, sqrt, atan2, degrees
from threading import Lock

from mcp.server.fastmcp import FastMCP
from database import get_db
from sqlalchemy import func
from models import (
    Restaurant,
    RestaurantTable,
    User,
    Booking,
    Reservation,
    Feedback,
)

# import logging
# import sys

# # Force all logs to stderr (required for MCP)
# logging.basicConfig(stream=sys.stderr)
# logger = logging.getLogger("fastmcp")
# logger.setLevel(logging.INFO)
# logger.handlers.clear()
# logger.addHandler(logging.StreamHandler(sys.stderr))

# high level:
# - Kept server-side logic deliberately minimal: pure DB queries and deterministic helpers only.
# - Replaced older model names (UserFeedback -> Feedback) to match current models.py.
# - Implemented explicit, well-documented tools required (1..11 list + helpers).
# - All NLP, sentiment, preference inference, and ranking logic remain on client/agent (services.py / Ollama).
# - Datetimes are handled as timezone-aware IST where possible; ISO strings are used for I/O.


# Global dictionary of restaurant locks
_RESTAURANT_LOCKS = {}

def get_restaurant_lock(restaurant_id: int) -> Lock:
    if restaurant_id not in _RESTAURANT_LOCKS:
        _RESTAURANT_LOCKS[restaurant_id] = Lock()
    return _RESTAURANT_LOCKS[restaurant_id]



mcp = FastMCP("Reservation-Agent")

# --------------------------- Helpers ---------------------------
IST = timezone(timedelta(hours=5, minutes=30))

def now_ist() -> datetime:
    return datetime.now(IST)


def iso_to_dt(s: str) -> datetime:
    """
    Convert ISO string to IST-aware datetime.
    - If input string has no timezone info, assume IST.
    - Always return an IST-aware datetime object.
    """
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def dt_to_iso(dt: datetime) -> str:
    """
    Convert a datetime to ISO 8601 string in IST timezone.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(IST).isoformat()


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return distance in kilometers between two lat/lon points."""
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * asin(sqrt(a))
    km = 6371 * c
    return km

def tables_needed(guests: int, table_size: int = 6) -> int:
    return ceil(guests / table_size)


def get_available_tables(db, restaurant_id: int, start_dt: datetime, end_dt: datetime):
    """
    Returns all tables in the restaurant that are free between start_dt and end_dt.
    Uses NOT EXISTS to avoid N+1 queries.
    """

    RT = RestaurantTable
    R  = Reservation
    B  = Booking

    # Subquery: find reservations that overlap with the requested window
    overlap_subq = (
        db.query(R.id)
        .join(B, R.booking_id == B.id)
        .filter(R.table_id == RT.id)
        .filter(
            ~(
                (B.end_dt <= start_dt) |   # booking ends before new one starts
                (B.start_dt >= end_dt)    # booking starts after new one ends
            )
        )
        .exists()
    )

    # Main query: all restaurant tables where NO overlapping reservation exists
    available = (
        db.query(RT)
        .filter(RT.restaurant_id == restaurant_id)
        .filter(~overlap_subq)        # NOT EXISTS (overlapping reservation)
        .all()
    )

    return available


def allocate_tables_transaction(
    db,
    user_id: int,
    restaurant_id: int,
    start_dt: datetime,
    end_dt: datetime,
    guests: int,
    allow_non_contiguous: bool = False
) -> Dict[str, Any]:

    # Restaurant-level lock to prevent double booking
    lock = get_restaurant_lock(restaurant_id)
    with lock:

        with db.begin():   # ← ADDED TRANSACTION BLOCK

            required = tables_needed(guests)

            # First find free tables
            free_tables = get_available_tables(db, restaurant_id, start_dt, end_dt)
            if len(free_tables) < required:
                return {"success": False, "error": "Not enough free tables", "reservations": []}

            # Choose tables (same logic as before)
            sorted_tables = sorted(free_tables, key=lambda t: t.table_no)
            chosen = []

            # Try contiguous
            for i in range(len(sorted_tables) - required + 1):
                block = sorted_tables[i : i + required]
                nums = [b.table_no for b in block]
                if all(nums[j+1] - nums[j] == 1 for j in range(len(nums)-1)):
                    chosen = block
                    break

            # If not contiguous, fallback
            if not chosen:
                if not allow_non_contiguous:
                    return {"success": False, "error": "No contiguous tables available", "reservations": []}
                chosen = sorted_tables[:required]

            # Simple re-check: ensure these tables are STILL free
            chosen_ids = [tbl.id for tbl in chosen]
            conflicts = (
                db.query(Reservation)
                .filter(Reservation.table_id.in_(chosen_ids))
                .join(Booking, Reservation.booking_id == Booking.id)
                .filter(
                    ~(
                        (Booking.end_dt <= start_dt) |
                        (Booking.start_dt >= end_dt)
                    )
                )
                .count()
            )

            if conflicts > 0:
                return {"success": False, "error": "Conflict detected, please try again", "reservations": []}

            # Create booking
            booking = Booking(
                user_id=user_id,
                restaurant_id=restaurant_id,
                start_dt=start_dt,
                end_dt=end_dt,
                guests=guests,
                status="confirmed",
                created_at=now_ist()
            )
            db.add(booking)
            db.flush()   # Get booking.id

            created_res_rows = []

            # Create reservation rows only with booking_id + table_id
            for tbl in chosen:
                res = Reservation(
                    booking_id=booking.id,
                    table_id=tbl.id,
                    created_at=now_ist()
                )
                db.add(res)
                db.flush()

                created_res_rows.append({
                    "reservation_id": res.id,
                    "table_no": tbl.table_no
                })

            return {
                "success": True,
                "message": "Booking created successfully",
                "booking_id": booking.id,
                "reservations": created_res_rows
            }

def center_of_area(area_name: str) -> Dict[str, Any]:
    """
    Compute the geographic centroid (spherical mean) of restaurants in the given area.

    Returns:
        {
            "success": True,
            "data": {"latitude": float, "longitude": float}
        }
        OR
        {
            "success": False,
            "error": "No restaurants found in area ..."
        }
    """
    try:
        db = next(get_db())
        rows = (
            db.query(Restaurant.latitude, Restaurant.longitude)
            .filter(Restaurant.area.ilike(f"%{area_name}%"))
            .all()
        )

        if not rows:
            return {
                "success": False,
                "error": f"No restaurants found in area '{area_name}'"
            }

        # Convert to radians for spherical mean
        x = y = z = 0.0
        for lat, lon in rows:
            lat_rad = radians(lat)
            lon_rad = radians(lon)
            x += cos(lat_rad) * cos(lon_rad)
            y += cos(lat_rad) * sin(lon_rad)
            z += sin(lat_rad)

        total = len(rows)
        x /= total
        y /= total
        z /= total

        lon_centroid = atan2(y, x)
        hyp = sqrt(x * x + y * y)
        lat_centroid = atan2(z, hyp)

        # Convert back to degrees
        lat_deg = degrees(lat_centroid)
        lon_deg = degrees(lon_centroid)

        return {
            "success": True,
            "data": {"latitude": lat_deg, "longitude": lon_deg}
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


# --------------------------- MCP Tools (server-side only) ---------------------------

@mcp.tool()
def check_availability_for_restaurant(restaurant_id: int, start_iso: str, end_iso: Optional[str] = None, guests: int = 1) -> Dict[str, Any]:
    """Check availability for a specific restaurant and time window.

    Returns available (bool) and next_slots (list iso) within next 3 hours if not available.
    This tool intentionally does NOT search by area; it focuses only on restaurant id + slot.
    """
    try:
        db = next(get_db())
        start_dt = iso_to_dt(start_iso)
        end_dt = iso_to_dt(end_iso) if end_iso else start_dt + timedelta(hours=2)

        free_tables = get_available_tables(db, restaurant_id, start_dt, end_dt)
        ok = len(free_tables) >= tables_needed(guests)

        next_slots = []
        if not ok:
            # look ahead up to 3 hours in 15-min steps
            gran = timedelta(minutes=15)
            limit = timedelta(hours=3)
            t = start_dt + gran
            collected = 0
            while t <= start_dt + limit and collected < 3:
                e = t + (end_dt - start_dt)
                ft = get_available_tables(db, restaurant_id, t, e)
                if len(ft) >= tables_needed(guests):
                    next_slots.append(dt_to_iso(t))
                    collected += 1
                t += gran

        # Determine final success based on availability
        if ok or len(next_slots) > 0:
            return {
                "success": True,
                "data": {
                    "restaurant_id": restaurant_id,
                    "requested_slot": {
                        "start_iso": dt_to_iso(start_dt),
                        "end_iso": dt_to_iso(end_dt)
                    },
                    "is_available_for_requested_slot": ok,
                    "next_available_slots": next_slots
                }
            }
        else:
            return {
                "success": False,
                "error": (
                    f"The requested slot is not available for restaurant {restaurant_id} "
                    f"and there are no nearby upcoming slots available."
                )
            }
    
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def get_restaurant_details_by_id(restaurant_id: int) -> Dict[str, Any]:
    """
    Return necessary details of a restaurant given its restaurant_id.

    Returns:
      success: True/False
      data: { id, name, area, cuisines, amenities }
      error: "...error message..."
    """
    try:
        db = next(get_db())
        r = db.query(Restaurant).filter(Restaurant.id == restaurant_id).first()

        if not r:
            return {
                "success": False,
                "error": f"Restaurant with ID {restaurant_id} not found"
            }

        return {
            "success": True,
            "data": {
                "id": r.id,
                "name": r.name,
                "area": r.area,
                "cuisines": [c.strip() for c in (r.cuisines or "").split(",") if c.strip()],
                "amenities": [a.strip() for a in (r.amenities or "").split(",") if a.strip()],
            }
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def get_restaurants_by_partial_name(name_query: str, limit: int = 5) -> Dict[str, Any]:
    """
    Return restaurant IDs matching a partial name search (case-insensitive).
    Useful when the client only knows the restaurant name, not the ID.

    Returns:
      success: True/False
      data: { restaurants: [ {id, name, area}, ... ] }
      error: "...error message..."
    """
    try:
        db = next(get_db())
        q = db.query(Restaurant).filter(Restaurant.name.ilike(f"%{name_query}%"))
        rows = q.limit(limit).all()

        if not rows:
            return {
                "success": False,
                "error": f"No restaurants found with name matching '{name_query}'"
            }

        results = []
        for r in rows:
            results.append({
                "id": r.id,
                "name": r.name,
                "area": r.area,
                "latitude": r.latitude,
                "longitude": r.longitude,
                "cuisines": [c.strip() for c in (r.cuisines or "").split(",") if c.strip()],
                "amenities": [a.strip() for a in (r.amenities or "").split(",") if a.strip()],
            })

        return {
            "success": True,
            "data": {
                "restaurants": results
            }
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def get_restaurants_in_area(area_name: str, limit: int = 50) -> Dict[str, Any]:
    """Return list of restaurants in an area (by exact area/area substring match)."""
    try:
        db = next(get_db())
        q = db.query(Restaurant).filter(Restaurant.area.ilike(f"%{area_name}%"))
        rows = q.limit(limit).all()

        if not rows:
            return {"success": False, "error": f"No restaurants found in area '{area_name}'"}

        results = []
        for r in rows:
            results.append({
                "id": r.id,
                "name": r.name,
                "area": r.area,
                "latitude": r.latitude,
                "longitude": r.longitude,
                "cuisines": [c.strip() for c in (r.cuisines or "").split(",") if c.strip()],
                "amenities": [a.strip() for a in (r.amenities or "").split(",") if a.strip()],
            })
        return {"success": True, "data": results}

    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def five_nearby_restaurants(
    area_name: Optional[str] = None,
    restaurant_id: Optional[int] = None,
    radius_km: float = 10.0
) -> Dict[str, Any]:
    """
    Return up to 5 restaurants nearest to a base point.

    Behavior:
    - Either `restaurant_id` or `area_name` must be provided.
    - If `restaurant_id` is provided, its lat/lon is used as the base point.
    - If `area_name` is provided, the centroid of that area's restaurants is used as the search center.
    - The DB is filtered by a lat/lon bounding box to approximate the radius.
    - Final results are sorted by Haversine distance to the base.
    """
    try:
        if not area_name and not restaurant_id:
            return {"success": False, "error": "Provide either area_name or restaurant_id"}

        db = next(get_db())

        # --- Determine base point (for final distance sorting) ---
        base_restaurant = None
        if restaurant_id:
            base_restaurant = db.query(Restaurant).filter(Restaurant.id == restaurant_id).first()
            if not base_restaurant:
                return {"success": False, "error": f"Restaurant ID {restaurant_id} not found"}
            base_lat, base_lon = base_restaurant.latitude, base_restaurant.longitude
        else:
            # If no restaurant_id, pick the first restaurant in the area as base
            base_restaurant = (
                db.query(Restaurant)
                .filter(Restaurant.area.ilike(f"%{area_name}%"))
                .order_by(Restaurant.id)
                .first()
            )
            if not base_restaurant:
                return {"success": False, "error": f"No restaurants found in area '{area_name}'"}
            base_lat, base_lon = base_restaurant.latitude, base_restaurant.longitude

        # Determine search centre: use area centroid if area_name provided, else base point
        if area_name:
            centre = center_of_area(area_name)
            if not centre.get("success", True):
                # fallback to base point
                centre_lat, centre_lon = base_lat, base_lon
            else:
                centre_data = centre.get("data", {})
                centre_lat = centre_data.get("latitude", base_lat)
                centre_lon = centre_data.get("longitude", base_lon)
        else:
            centre_lat, centre_lon = base_lat, base_lon

        # Compute bounding box around centre to approximate radius_km
        # Approximation: 1 deg latitude ~= 111.32 km
        lat_deg = radius_km / 111.32
        # 1 deg longitude ~= 111.32 * cos(lat) km
        lon_deg = radius_km / (111.32 * max(0.000001, abs(cos(radians(centre_lat)))))

        min_lat, max_lat = centre_lat - lat_deg, centre_lat + lat_deg
        min_lon, max_lon = centre_lon - lon_deg, centre_lon + lon_deg

        # Query DB for restaurants within bounding box (fast prefilter)
        candidates = (
            db.query(Restaurant)
            .filter(Restaurant.latitude >= min_lat, Restaurant.latitude <= max_lat)
            .filter(Restaurant.longitude >= min_lon, Restaurant.longitude <= max_lon)
            .all()
        )

        if not candidates:
            return {
                "success": False,
                "error": f"No nearby restaurants found within {radius_km} km of area '{area_name or 'base location'}'"
            }

        # --- Compute Haversine distances and sort ---
        scored: List[Tuple[float, Restaurant]] = []
        for c in candidates:
            # skip the base restaurant itself
            if base_restaurant and c.id == base_restaurant.id:
                continue
            d = haversine(base_lat, base_lon, c.latitude, c.longitude)
            if d <= radius_km:
                scored.append((d, c))

        if not scored:
            return {"success": False, "error": f"No restaurants found within {radius_km} km"}

        scored.sort(key=lambda x: x[0])

        # --- Prepare output ---
        results = []
        for d, r in scored[:5]:
            results.append({
                "id": r.id,
                "name": r.name,
                "area": r.area,
                "distance_km": round(d, 2),
                "cuisines": [c.strip() for c in (r.cuisines or "").split(",") if c.strip()],
                "amenities": [a.strip() for a in (r.amenities or "").split(",") if a.strip()],
            })

        return {"success": True, "data": results}

    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def latest_5_user_feedback(user_id: int) -> Dict[str, Any]:
    """
    Return the latest 5 feedback entries for a user.
    Uses booking-based feedback.
    """
    try:
        db = next(get_db())

        rows = (
            db.query(Feedback)
            .filter(Feedback.user_id == user_id)
            .order_by(Feedback.created_at.desc())
            .limit(5)
            .all()
        )

        result = []
        for f in rows:
            result.append({
                "feedback_id": f.id,
                "booking_id": f.booking_id,
                "user_id": f.user_id,
                "restaurant_id": f.restaurant_id,
                "stars": f.stars,
                "text": f.text,
                "created_at": dt_to_iso(f.created_at),
            })

        return {"success": True, "data": result}

    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def latest_5_restaurant_feedback(restaurant_id: int) -> Dict[str, Any]:
    """
    Return the latest 5 feedback entries for a restaurant.
    Uses booking-based feedback.
    """
    try:
        db = next(get_db())

        rows = (
            db.query(Feedback)
            .filter(Feedback.restaurant_id == restaurant_id)
            .order_by(Feedback.created_at.desc())
            .limit(5)
            .all()
        )

        result = []
        for f in rows:
            result.append({
                "feedback_id": f.id,
                "booking_id": f.booking_id,
                "user_id": f.user_id,
                "restaurant_id": f.restaurant_id,
                "stars": f.stars,
                "text": f.text,
                "created_at": dt_to_iso(f.created_at),
            })

        return {"success": True, "data": result}

    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def make_reservation_tool(
    user_id: int,
    restaurant_id: int,
    start_iso: str,
    end_iso: Optional[str],
    guests: int = 1,
    allow_non_contiguous: bool = False
) -> Dict[str, Any]:
    """
    Try to make a reservation (Booking + table Reservations).
    Returns:
      - success: bool
      - data: { booking_id, reservations, message }   (on success)
      - error: "..."                                  (on failure)
    """
    try:
        db = next(get_db())

        # parse datetimes
        start_dt = iso_to_dt(start_iso)
        end_dt = iso_to_dt(end_iso) if end_iso else start_dt + timedelta(hours=2)

        # Basic validation
        if start_dt >= end_dt:
            return {"success": False, "error": "Invalid time window: start time must be before end time"}
        if guests <= 0:
            return {"success": False, "error": "Invalid guest count"}

        result = allocate_tables_transaction(
            db,
            user_id,
            restaurant_id,
            start_dt,
            end_dt,
            guests,
            allow_non_contiguous
        )

        # ---- SUCCESS CASE ----
        if result.get("success"):
            return {
                "success": True,
                "data": {
                    "message": result.get("message"),
                    "booking_id": result.get("booking_id"),
                    "reservations": result.get("reservations", [])
                }
            }

        # ---- FAILURE CASE ----
        return {
            "success": False,
            "error": result.get("error") or result.get("message") or "Table Allocation failed. Reservation failed"
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def cancel_reservation_tool(booking_id: int, user_id: int) -> Dict[str, Any]:
    """
    Cancel a booking.
    Deletes the Booking + all child Reservations (cascade).
    Returns:
      success: bool
      data: { message, booking_id, reservation_ids }
      or
      error: "..."
    """
    try:
        db = next(get_db())

        with db.begin():
            b = db.query(Booking).filter(Booking.id == booking_id).first()
            if not b:
                return {"success": False, "error": "Booking ID not found"}

            if b.user_id != user_id:
                return {"success": False, "error": "User not authorized to cancel this booking"}

            # Capture reservation IDs before deleting
            deleted_res_ids = [r.id for r in b.reservations]

            # Delete booking → cascade deletes reservations
            db.delete(b)

            return {
                "success": True,
                "data": {
                    "message": "Booking cancelled successfully",
                    "booking_id": booking_id,
                    "reservation_ids": deleted_res_ids
                }
            }
    
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def submit_feedback_tool(
    booking_id: int,
    user_id: int,
    stars: int,
    text: Optional[str] = None
) -> Dict[str, Any]:
    """
    Submit feedback for a booking.
    One feedback per booking (booking.feedback relationship).
    Returns:
      success: True, data: {...}
      or
      success: False, error: "..."
    """
    try:
        db = next(get_db())

        # Validate stars
        if stars < 1 or stars > 5:
            return {"success": False, "error": "Stars must be between 1 and 5"}

        with db.begin():
            # Fetch booking
            b = db.query(Booking).filter(Booking.id == booking_id).first()
            if not b:
                return {"success": False, "error": "Booking ID not found"}

            # Verify user owns the booking
            if b.user_id != user_id:
                return {"success": False, "error": "User not authorized to submit feedback for this booking"}

            # If feedback already exists, update it
            if b.feedback:
                f = b.feedback
                f.stars = stars
                f.text = text
                f.created_at = now_ist()

            # Else create new feedback
            else:
                f = Feedback(
                    user_id=b.user_id,
                    restaurant_id=b.restaurant_id,
                    booking_id=b.id,
                    stars=stars,
                    text=text,
                    created_at=now_ist()
                )
                db.add(f)

            # Auto-committed by db.begin()
            return {
                "success": True,
                "data": {
                    "message": "Feedback submitted successfully",
                    "booking_id": booking_id,
                    "stars": stars,
                    "text": text
                }
            }

    except Exception as e:
        return {"success": False, "error": str(e)}


# simple getters for amenities/cuisines and universal lists

@mcp.tool()
def get_rating_for_restaurant(restaurant_id: int) -> Dict[str, Any]:
    """
    Return the rating of the given restaurant.
    Returns standardized JSON:
      - success: bool
      - data: { rating: float } on success
      - error: "..." on failure
    """
    try:
        db = next(get_db())
        r = db.query(Restaurant).filter(Restaurant.id == restaurant_id).first()

        if not r:
            return {"success": False, "error": f"Restaurant with ID {restaurant_id} not found"}

        return {"success": True, "data": {"rating": r.rating}}

    except Exception as e:
        return {"success": False, "error": str(e)}

@mcp.tool()
def get_amenities_for_restaurant(restaurant_id: int) -> Dict[str, Any]:
    """
    Return the list of amenities for a given restaurant.
    Returns standardized JSON:
      - success: bool
      - data: { amenities: [...] } on success
      - error: "..." on failure
    """
    try:
        db = next(get_db())
        r = db.query(Restaurant).filter(Restaurant.id == restaurant_id).first()

        if not r:
            return {"success": False, "error": f"Restaurant with ID {restaurant_id} not found"}

        if not r.amenities:
            return {"success": True, "data": {"amenities": []}}

        amenities = [a.strip() for a in r.amenities.split(",") if a.strip()]
        return {"success": True, "data": {"amenities": amenities}}

    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def get_cuisines_for_restaurant(restaurant_id: int) -> Dict[str, Any]:
    """
    Return the list of cuisines for a given restaurant.
    Returns standardized JSON:
      - success: bool
      - data: { cuisines: [...] } on success
      - error: "..." on failure
    """
    try:
        db = next(get_db())
        r = db.query(Restaurant).filter(Restaurant.id == restaurant_id).first()

        if not r:
            return {"success": False, "error": f"Restaurant with ID {restaurant_id} not found"}

        if not r.cuisines:
            return {"success": True, "data": {"cuisines": []}}

        cuisines = [c.strip() for c in r.cuisines.split(",") if c.strip()]
        return {"success": True, "data": {"cuisines": cuisines}}

    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def get_all_amenities() -> Dict[str, Any]:
    """
    Return the canonical list of all possible amenities.
    Returns standardized JSON:
      - success: bool
      - data: { amenities: [...] }
    """
    try:
        amenities = [
            "WiFi",
            "Parking",
            "AC",
            "Outdoor Seating",
            "Rooftop",
            "Live Music",
            "Valet",
            "Pet Friendly",
            "vegetarian options",
        ]
        return {"success": True, "data": {"amenities": amenities}}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def get_all_cuisines() -> Dict[str, Any]:
    """
    Return the canonical list of all possible cuisines.
    Returns standardized JSON:
      - success: bool
      - data: { cuisines: [...] }
    """
    try:
        cuisines = [
            "Italian",
            "Indian",
            "Chinese",
            "Mexican",
            "Continental",
            "South Indian",
        ]
        return {"success": True, "data": {"cuisines": cuisines}}
    except Exception as e:
        return {"success": False, "error": str(e)}


# -------------------------------------------------------------
# End of tool definitions
# -------------------------------------------------------------

# if __name__ == "__main__":
#     import asyncio
#     asyncio.run(mcp.run(transport="stdio"))

if __name__ == "__main__":
    mcp.run(transport="stdio")
