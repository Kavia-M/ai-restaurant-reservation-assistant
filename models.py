from datetime import datetime, timezone, timedelta
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, ForeignKey, Text, JSON, Index
)
from sqlalchemy.orm import relationship
from database import Base

# Define IST timezone
IST = timezone(timedelta(hours=5, minutes=30))

# =====================================================
# RESTAURANT
# =====================================================
class Restaurant(Base):
    __tablename__ = "restaurants"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    area = Column(String, nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    cuisines = Column(String, nullable=True)
    rating = Column(Float, default=0.0)
    amenities = Column(String, nullable=True)  # comma-separated amenities
#    daily_specials = Column(JSON, nullable=True)  # JSON dict: {"Monday": "Dish, Dish", ...}
    created_at = Column(DateTime, default=lambda: datetime.now(IST))

    tables = relationship("RestaurantTable", back_populates="restaurant", cascade="all, delete-orphan")
    bookings = relationship("Booking", back_populates="restaurant", cascade="all, delete-orphan")
    feedbacks = relationship("Feedback", back_populates="restaurant", cascade="all, delete-orphan")

# =====================================================
# RESTAURANT TABLE
# =====================================================
class RestaurantTable(Base):
    __tablename__ = "restaurant_tables"
    id = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False, index=True)
    table_no = Column(Integer, nullable=False)
    seats = Column(Integer, default=6)

    restaurant = relationship("Restaurant", back_populates="tables")
    reservations = relationship("Reservation", back_populates="table")

# =====================================================
# USER
# =====================================================
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=True)
    phone = Column(String, unique=True, nullable=False, index=True)
    email = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(IST))

    bookings = relationship("Booking", back_populates="user", cascade="all, delete-orphan")
    feedbacks = relationship("Feedback", back_populates="user", cascade="all, delete-orphan")

# =====================================================
# BOOKING (PARENT)
# =====================================================
class Booking(Base):
    __tablename__ = "bookings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False, index=True)

    start_dt = Column(DateTime, nullable=False)
    end_dt = Column(DateTime, nullable=False)
    guests = Column(Integer, nullable=False)
    status = Column(String, default="confirmed")
    created_at = Column(DateTime, default=lambda: datetime.now(IST))

    user = relationship("User", back_populates="bookings")
    restaurant = relationship("Restaurant", back_populates="bookings")
    reservations = relationship("Reservation", back_populates="booking", cascade="all, delete-orphan")
    feedback = relationship("Feedback", back_populates="booking", uselist=False, cascade="all, delete-orphan")

# =====================================================
# RESERVATION (CHILD) - ONLY TABLE ASSIGNMENT
# =====================================================
class Reservation(Base):
    __tablename__ = "reservations"
    id = Column(Integer, primary_key=True, index=True)

    booking_id = Column(Integer, ForeignKey("bookings.id"), nullable=False, index=True)
    table_id = Column(Integer, ForeignKey("restaurant_tables.id"), nullable=False, index=True)

    created_at = Column(DateTime, default=lambda: datetime.now(IST))

    booking = relationship("Booking", back_populates="reservations")
    table = relationship("RestaurantTable", back_populates="reservations")

# =====================================================
# FEEDBACK (ONLY LEVEL = BOOKING)
# =====================================================
class Feedback(Base):
    __tablename__ = "feedbacks"
    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=False, index=True)
    booking_id = Column(Integer, ForeignKey("bookings.id"), nullable=False, index=True)

    stars = Column(Integer, nullable=True)
    text = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(IST))

    user = relationship("User", back_populates="feedbacks")
    restaurant = relationship("Restaurant", back_populates="feedbacks")
    booking = relationship("Booking", back_populates="feedback")

# =====================================================
# INDEXES
# =====================================================
Index('ix_reservation_booking_table', Reservation.booking_id, Reservation.table_id)
Index('ix_feedback_restaurant_created', Feedback.restaurant_id, Feedback.created_at)
Index('ix_feedback_user_created', Feedback.user_id, Feedback.created_at)
Index('ix_restauranttable_restaurant', RestaurantTable.restaurant_id)
