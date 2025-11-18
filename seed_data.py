# seed_data.py
"""
Deterministic seed script for the Restaurant Reservation demo.

- Creates 50 restaurants with predictable names and coordinates.
- Restaurant ID 1 is fixed: GoodFoods Cravings Adyar 01 in Adyar with
  cuisines "Indian, Chinese" and amenities "WiFi, Outdoor Seating, Parking".
- Creates restaurant tables (5-15 per restaurant) deterministically.
- Creates users (10).
- Creates fixed bookings for User 1 (IDs 1,2,3).
- Creates many bookings on Dec 1 that fill Restaurant 1 for the 19:00 slot
  (booking IDs 51..60) so searches for Dec 1, 19:00 likely return unavailable.
- Creates feedback entries:
    - 7 feedbacks about Restaurant 1 (only referencing its cuisines/amenities)
    - 3 feedbacks from User 1 (expressing likes/dislikes aligned to Restaurant 1)
    - feedbacks for one nearby restaurant matching user 1 preferences
- Uses IST timezone and deterministic random seed.

Run:
    python seed_data.py
"""
import random
from datetime import datetime, timedelta, timezone, time
from math import ceil
from database import SessionLocal, engine
from models import Base, Restaurant, RestaurantTable, User, Booking, Reservation, Feedback

# IST timezone
IST = timezone(timedelta(hours=5, minutes=30))

def now_ist():
    return datetime.now(IST)

def dt_ist(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=IST)

def iso(dt):
    return dt.astimezone(IST).isoformat()

# deterministic seed
random.seed(42)

# fixed coordinate for Restaurant 1 (Adyar area)
R1_LAT = 13.0100
R1_LON = 80.2200

# nearby offsets (in degrees — small)
NEARBY_COORDS = [
    (13.0125, 80.2218),
    (13.0078, 80.2185),
    (13.0156, 80.2240),
    (13.0090, 80.2225),
    (13.0132, 80.2175)
]

# catchy words (cycled deterministically)
CATCHY = ["Cravings", "Tasty", "Delight", "Feast", "Bistro", "Aroma", "Spice", "Grill", "Treats", "Hub"]

AREAS = [
    "Adyar", "Velachery", "T Nagar", "Anna Nagar", "Perungudi",
    "KK Nagar", "Tambaram", "Nungambakkam", "Guindy", "Mylapore"
]

# helper to produce 2-digit id strings
def id2(n):
    return f"{n:02d}"

def create_restaurant_name(word, area, rid):
    return f"GoodFoods {word} {area} {id2(rid)}"

def seed_data():
    db = SessionLocal()
    try:
        # drop & recreate schema
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)

        # --- Restaurants ---
        restaurants = []
        # We'll place restaurant 1 deterministically first
        r1 = Restaurant(
            id=1,
            name="GoodFoods Cravings Adyar 01",
            area="Adyar",
            latitude=R1_LAT,
            longitude=R1_LON,
            cuisines="Indian,Chinese",
            rating=4.3,
            amenities="WiFi, Outdoor Seating, Parking",
            created_at=now_ist()
        )
        db.add(r1)
        restaurants.append(r1)
        db.flush()  # ensure id=1 exists

        # Decide 5 nearby restaurant IDs (non-sequential, deterministic)
        nearby_ids = [3, 7, 12, 18, 25]  # chosen deterministically
        # create restaurants up to 50
        next_coord_idx = 0
        for rid in range(2, 51):
            # pick catchy word deterministically
            word = CATCHY[(rid - 1) % len(CATCHY)]
            # choose area: make many in different areas; make some in Adyar for nearby ones
            if rid in nearby_ids:
                area = "Adyar"
                lat, lon = NEARBY_COORDS[next_coord_idx]
                next_coord_idx += 1
            else:
                # spread across Chennai-like coordinates around a center
                # use small deterministic offsets
                area = AREAS[rid % len(AREAS)]
                # generate reproducible but varied coords near city center
                lat = 13.05 + ((rid * 37) % 100 - 50) / 1000.0  # small spread
                lon = 80.25 + ((rid * 73) % 100 - 50) / 1000.0
            name = create_restaurant_name(word, area, rid)
            # For variety, choose cuisines from a fixed small list
            CUISINES_POOL = ["Italian", "Indian", "Chinese", "Mexican", "Continental", "South Indian"]
            cuisines = ",".join(random.sample(CUISINES_POOL, k=2))
            # amenities pool
            AMEN_POOL = ["WiFi", "Parking", "AC", "Outdoor Seating", "Rooftop", "Live Music", "Valet", "Pet Friendly"]
            # ensure restaurants in Adyar often have outdoor seating to be compatible with user prefs
            if area == "Adyar":
                amenities = "Outdoor Seating, WiFi"
            else:
                amenities = ", ".join(random.sample(AMEN_POOL, k=random.randint(1, 3)))
            rating = round(3.0 + ((rid * 17) % 20) / 5.0, 1)
            r = Restaurant(
                id=rid,
                name=name,
                area=area,
                latitude=lat,
                longitude=lon,
                cuisines=cuisines,
                rating=rating,
                amenities=amenities,
                created_at=now_ist()
            )
            db.add(r)
            restaurants.append(r)
        db.commit()

        # --- Tables per restaurant ---
        # deterministic number: 8 tables for restaurant 1 to make fillable; others vary 5-12
        for r in db.query(Restaurant).all():
            if r.id == 1:
                num_tables = 10  # fixed for R1
            else:
                # deterministic but varied
                num_tables = 5 + ((r.id * 13) % 8)  # gives 5..12
            for tno in range(1, num_tables + 1):
                tbl = RestaurantTable(restaurant_id=r.id, table_no=tno, seats=6)
                db.add(tbl)
        db.commit()

        # --- Users ---
        users = []
        for i in range(1, 11):
            u = User(
                id=i,
                name=f"User {i}",
                phone=f"99999{i:05}",
                email=f"user{i}@example.com",
                created_at=now_ist()
            )
            db.add(u)
            users.append(u)
        db.commit()

        # --- Bookings & Reservations ---
        # 1) Create three fixed bookings for User 1 with ids 1,2,3
        #    Booking 1 -> Restaurant 1, near future slot (tomorrow 19:00)
        #    Booking 2 -> one nearby restaurant (pick id 12)
        #    Booking 3 -> Restaurant 1 (different time)
        tomorrow = (now_ist() + timedelta(days=1)).date()
        b1 = Booking(
            id=1,
            user_id=1,
            restaurant_id=1,
            start_dt=dt_ist(tomorrow.year, tomorrow.month, tomorrow.day, 19, 0),
            end_dt=dt_ist(tomorrow.year, tomorrow.month, tomorrow.day, 21, 0),
            guests=4,
            status="confirmed",
            created_at=now_ist()
        )
        db.add(b1)
        db.flush()
        # allocate 1 table for booking 1 (choose table_no 1)
        t_row = db.query(RestaurantTable).filter_by(restaurant_id=1, table_no=1).first()
        r1_res = Reservation(booking_id=b1.id, table_id=t_row.id, created_at=now_ist())
        db.add(r1_res)

        b2 = Booking(
            id=2,
            user_id=1,
            restaurant_id=12,  # one of the nearby restaurants chosen to match user prefs
            start_dt=dt_ist(tomorrow.year, tomorrow.month, tomorrow.day, 20, 0),
            end_dt=dt_ist(tomorrow.year, tomorrow.month, tomorrow.day, 22, 0),
            guests=2,
            status="confirmed",
            created_at=now_ist()
        )
        db.add(b2)
        db.flush()
        t_row2 = db.query(RestaurantTable).filter_by(restaurant_id=12).first()
        if t_row2:
            db.add(Reservation(booking_id=b2.id, table_id=t_row2.id, created_at=now_ist()))

        b3 = Booking(
            id=3,
            user_id=1,
            restaurant_id=1,
            start_dt=dt_ist(tomorrow.year, tomorrow.month, tomorrow.day, 12, 30),
            end_dt=dt_ist(tomorrow.year, tomorrow.month, tomorrow.day, 14, 30),
            guests=2,
            status="confirmed",
            created_at=now_ist()
        )
        db.add(b3)
        db.flush()
        # allocate table_no 2 for b3
        t_row3 = db.query(RestaurantTable).filter_by(restaurant_id=1, table_no=2).first()
        if t_row3:
            db.add(Reservation(booking_id=b3.id, table_id=t_row3.id, created_at=now_ist()))

        db.commit()

        # 2) Create many bookings on Dec 1 to fill 19:00-21:00 slot for Restaurant 1
        # Choose deterministic booking ids 51..60
        dec1 = datetime(now_ist().year, 12, 1, tzinfo=IST).date()
        # get all table ids for restaurant 1
        tables_r1 = db.query(RestaurantTable).filter_by(restaurant_id=1).order_by(RestaurantTable.table_no).all()
        # We'll create bookings 51.. up to fill as many tables as we want (limit by available tables)
        busy_booking_ids = list(range(51, 51 + min(len(tables_r1), 10)))  # up to 10 bookings
        for idx, bid in enumerate(busy_booking_ids):
            start = dt_ist(dec1.year, dec1.month, dec1.day, 19, 0)
            end = dt_ist(dec1.year, dec1.month, dec1.day, 21, 0)
            bk = Booking(
                id=bid,
                user_id=((idx % 10) + 2),  # other users
                restaurant_id=1,
                start_dt=start,
                end_dt=end,
                guests=6,
                status="confirmed",
                created_at=now_ist()
            )
            db.add(bk)
            db.flush()
            # assign table idx (if available)
            if idx < len(tables_r1):
                tbl = tables_r1[idx]
                db.add(Reservation(booking_id=bk.id, table_id=tbl.id, created_at=now_ist()))
        db.commit()

        # 3) Misc bookings across restaurants to simulate activity (few deterministic ones)
        other_booking_id = 100
        for rid in [2, 4, 5, 8, 20]:
            start = now_ist() + timedelta(days=(rid % 5), hours=18)
            bk = Booking(
                # let id auto-increment for these (don't force IDs)
                user_id=((rid % 10) + 1),
                restaurant_id=rid,
                start_dt=start,
                end_dt=start + timedelta(hours=2),
                guests=2 + (rid % 3),
                status="confirmed",
                created_at=now_ist()
            )
            db.add(bk)
            db.flush()
            tbl = db.query(RestaurantTable).filter_by(restaurant_id=rid).first()
            if tbl:
                db.add(Reservation(booking_id=bk.id, table_id=tbl.id, created_at=now_ist()))
        db.commit()

        # --- Feedbacks ---
        # Restaurant 1 feedbacks (7 entries) — MUST reference only its cuisines & amenities
        r1_feedback_texts = [
            "Loved the Indian thali here and the outdoor seating made the evening delightful.",
            "Chinese noodles were flavorful, but WiFi was a bit slow during my visit.",
            "Great parking and neat AC dining area. The biryani (Indian) was top notch.",
            "Service was slow that night, but the veg Manchurian was tasty.",
            "Outdoor seating is fantastic for a weekend dinner; kid-friendly too.",
            "I expected faster WiFi; food quality is normally good though.",
            "Good value for Indian and Chinese combos; staff helpful with parking guidance."
        ]
        # generate 7 feedbacks for restaurant 1 (from different users deterministically)
        fb_users = [2, 3, 4, 5, 6, 7, 8]
        for i, u_id in enumerate(fb_users):
            fb = Feedback(
                user_id=u_id,
                restaurant_id=1,
                booking_id=(1 if i == 0 else None) or None,  # sometimes link to booking 1 for demonstration
                stars=4 if i % 3 != 0 else 3,
                text=r1_feedback_texts[i],
                created_at=now_ist() - timedelta(days=(i+1))
            )
            db.add(fb)
        db.commit()

        # User 1 personal feedbacks (3) — these express likes/dislikes and match Restaurant 1 offerings
        user1_fb_texts = [
            "I love Indian food and always prefer outdoor seating when the weather is good.",
            "Not a fan of slow WiFi; I like places with reliable internet and good AC.",
            "My favourite is spicy Chinese and a quiet corner — I dislike noisy restaurants."
        ]
        # attach them to the 3 user bookings (booking ids 1,2,3)
        u1_booking_map = [1, 2, 3]
        for idx, b_id in enumerate(u1_booking_map):
            fb = Feedback(
                user_id=1,
                restaurant_id=(db.query(Booking).filter_by(id=b_id).first().restaurant_id),
                booking_id=b_id,
                stars=5 if idx == 0 else (4 if idx == 1 else 3),
                text=user1_fb_texts[idx],
                created_at=now_ist() - timedelta(days=idx)
            )
            db.add(fb)
        db.commit()

        # Create feedbacks for one of the nearby restaurants to match user1 preferences
        # choose nearby id 12 (already used for booking 2)
        nearby_match_texts = [
            "Excellent Italian and Indian fusion; outdoor seating with shaded umbrellas was perfect.",
            "Clean parking and great outdoor seating — aligns with what I like.",
            "Food quality is excellent; staff were attentive."
        ]
        for i, txt in enumerate(nearby_match_texts):
            fb = Feedback(
                user_id=(9 + i) % 10 + 1,
                restaurant_id=12,
                booking_id=None,
                stars=5,
                text=txt,
                created_at=now_ist() - timedelta(days=2+i)
            )
            db.add(fb)
        db.commit()

        # --- Additional feedbacks for other restaurants (small mix) ---
        # Keep it minimal but deterministic
        sample_texts = [
            "Good ambience and food.",
            "Service could be faster.",
            "Loved the desserts here.",
            "Parking was a problem during peak hours."
        ]
        for rid in [2, 4, 7, 18, 25]:
            fb = Feedback(
                user_id=(rid % 10) + 1,
                restaurant_id=rid,
                booking_id=None,
                stars=4 if rid % 2 == 0 else 3,
                text=sample_texts[rid % len(sample_texts)],
                created_at=now_ist() - timedelta(days=(rid % 5))
            )
            db.add(fb)
        db.commit()

        print("Seeding completed successfully.")
        # --- Summary printout for demo verification ---
        print("=== DEMO SUMMARY ===")
        print("Restaurant 1:")
        print("  ID: 1")
        print("  Name: GoodFoods Cravings Adyar 01")
        print("  Area: Adyar")
        print("  Cuisines: Indian,Chinese")
        print("  Amenities: WiFi, Outdoor Seating, Parking")
        print("  Coordinates: lat=", R1_LAT, " lon=", R1_LON)
        print()
        print("Nearby restaurants (IDs):", nearby_ids)
        print()
        print("User 1 bookings (fixed IDs): 1, 2, 3")
        print("Dec 1 busy booking IDs for Restaurant 1 (occupied 19:00-21:00):", busy_booking_ids)
        print()
        print("Restaurant 1 feedback excerpts:")
        for t in r1_feedback_texts:
            print(" -", t)
        print()
        print("User 1 feedbacks (3):")
        for t in user1_fb_texts:
            print(" -", t)
        print("====================")
    except Exception as e:
        print("Error during seeding:", e)
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    seed_data()
