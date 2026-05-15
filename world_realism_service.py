import random
import secrets
from datetime import datetime, timedelta

# Realistic GTA-style data
STREET_PREFIXES = [
    'Grove', 'Integrity', 'Magellan', 'Pillbox', 'Del Perro', 'Vespucci',
    'Innocence', 'Prosperity', 'Cougar', 'Ginger', 'Amarillo', 'Paleto',
    'Sandy', 'Mirror', 'Rockford', 'Maze', 'Vinewood', 'Downtown'
]

STREET_SUFFIXES = [
    'Street', 'Avenue', 'Boulevard', 'Lane', 'Drive', 'Road', 'Way',
    'Court', 'Circle', 'Plaza', 'Terrace', 'Heights', 'Park'
]

NEIGHBORHOODS = [
    'Grove Street', 'Ballas Territory', 'Downtown', 'Vinewood', 'Pillbox Hill',
    'Del Perro', 'Vespucci', 'Sandy Shores', 'Paleto Bay', 'Grapeseed',
    'Chumash', 'Blaine County', 'Mirror Park', 'Rockford Hills', 'Maze Bank',
    'Rancho', 'Strawberry', 'Davis', 'Chamberlain Hills', 'Textile City'
]

BUSINESS_TYPES = [
    'Convenience Store', 'Bar', 'Restaurant', 'Nightclub', 'Garage',
    'Laundromat', 'Pawn Shop', 'Gun Store', 'Tattoo Parlor', 'Barber Shop',
    'Clothing Store', 'Electronics Store', 'Pharmacy', 'Diner', 'Cafe',
    'Gym', 'Bowling Alley', 'Arcade', 'Hotel', 'Motel', 'Strip Club',
    'Bail Bonds', 'Lawyer Office', 'Clinic', 'Warehouse'
]

BUSINESS_NAMES = [
    'The Rusty Nail', 'Cluckin Bell', 'Burger Shot', 'Pizza Stack',
    'Vanilla Unicorn', 'The Lost MC', 'Tequila La', 'Bahama Mamas',
    'Gentry Manor', 'Maze Bank', 'Pillbox Medical', 'Rockford Hills Clinic',
    'Ammu-Nation', 'Binco', 'Ponsonbys', 'Suburban', 'Discount Store',
    'Haircut Harry', 'Barber Shop', 'Tattoo Parlor', 'Gym', 'Arcade'
]

VEHICLE_MAKES = [
    'Baller', 'Blista', 'Dilettante', 'Fugitive', 'Granger', 'Habanero',
    'Jackal', 'Khamelion', 'Landstalker', 'Oracle', 'Patriot', 'Rocoto',
    'Rumpo', 'Serrano', 'Tailgater', 'Tornado', 'Warrener', 'Washington',
    'Asea', 'Asterope', 'Cog55', 'Cognoscenti', 'Dubsta', 'Granger',
    'Huntley', 'Landstalker', 'Mammoth', 'Patriot', 'Rocoto', 'Serrano'
]

VEHICLE_COLORS = [
    'Black', 'White', 'Gray', 'Silver', 'Red', 'Blue', 'Green', 'Yellow',
    'Orange', 'Purple', 'Brown', 'Gold', 'Lime', 'Cyan', 'Pink', 'Maroon',
    'Navy', 'Teal', 'Olive', 'Beige', 'Charcoal', 'Crimson', 'Emerald'
]

FIRST_NAMES = {
    'male': [
        'Marcus', 'Franklin', 'Trevor', 'Michael', 'James', 'David', 'Robert',
        'John', 'William', 'Richard', 'Joseph', 'Thomas', 'Charles', 'Christopher',
        'Daniel', 'Matthew', 'Anthony', 'Donald', 'Steven', 'Paul', 'Andrew',
        'Joshua', 'Kenneth', 'Kevin', 'Brian', 'George', 'Edward', 'Ronald',
        'Timothy', 'Jason', 'Jeffrey', 'Ryan', 'Jacob', 'Gary', 'Nicholas',
        'Eric', 'Jonathan', 'Stephen', 'Larry', 'Justin', 'Scott', 'Brandon'
    ],
    'female': [
        'Mary', 'Patricia', 'Jennifer', 'Linda', 'Barbara', 'Elizabeth', 'Susan',
        'Jessica', 'Sarah', 'Karen', 'Nancy', 'Lisa', 'Betty', 'Margaret',
        'Sandra', 'Ashley', 'Kimberly', 'Emily', 'Donna', 'Michelle', 'Dorothy',
        'Carol', 'Amanda', 'Melissa', 'Deborah', 'Stephanie', 'Rebecca', 'Sharon',
        'Laura', 'Cynthia', 'Kathleen', 'Amy', 'Angela', 'Shirley', 'Anna',
        'Brenda', 'Pamela', 'Emma', 'Nicole', 'Helen', 'Samantha', 'Katherine'
    ]
}

LAST_NAMES = [
    'Smith', 'Johnson', 'Williams', 'Brown', 'Jones', 'Garcia', 'Miller',
    'Davis', 'Rodriguez', 'Martinez', 'Hernandez', 'Lopez', 'Gonzalez',
    'Wilson', 'Anderson', 'Thomas', 'Taylor', 'Moore', 'Jackson', 'Martin',
    'Lee', 'Perez', 'Thompson', 'White', 'Harris', 'Sanchez', 'Clark',
    'Ramirez', 'Lewis', 'Robinson', 'Young', 'Allen', 'King', 'Wright',
    'Scott', 'Torres', 'Peterson', 'Phillips', 'Campbell', 'Parker', 'Evans',
    'Edwards', 'Collins', 'Reyes', 'Stewart', 'Morris', 'Morales', 'Murphy'
]

def generate_address(neighborhood=None):
    """Generate a realistic GTA-style address."""
    if not neighborhood:
        neighborhood = random.choice(NEIGHBORHOODS)

    street_prefix = random.choice(STREET_PREFIXES)
    street_suffix = random.choice(STREET_SUFFIXES)
    number = random.randint(100, 9999)

    street = f"{street_prefix} {street_suffix}"
    return f"{number} {street}, {neighborhood}"

def generate_plate():
    """Generate a realistic GTA-style license plate."""
    formats = [
        lambda: f"{random.randint(1,9)}{random.randint(0,9)}{random.randint(0,9)} {random.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ')}{random.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ')}{random.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ')}",
        lambda: f"{random.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ')}{random.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ')}{random.randint(10,99)} {random.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ')}{random.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ')}{random.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ')}",
        lambda: f"{random.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ')}{random.randint(0,9)}{random.randint(0,9)} {random.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ')}{random.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ')}{random.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ')}",
    ]
    return random.choice(formats)()

def generate_vehicle():
    """Generate a random vehicle."""
    make = random.choice(VEHICLE_MAKES)
    color = random.choice(VEHICLE_COLORS)
    plate = generate_plate()
    vin = f"VIN{secrets.token_hex(8).upper()}"

    return {
        'make': make,
        'color': color,
        'plate': plate,
        'vin': vin,
    }

def generate_business(neighborhood=None):
    """Generate a random business."""
    if not neighborhood:
        neighborhood = random.choice(NEIGHBORHOODS)

    business_type = random.choice(BUSINESS_TYPES)
    business_name = random.choice(BUSINESS_NAMES)
    address = generate_address(neighborhood)

    return {
        'name': business_name,
        'type': business_type,
        'address': address,
        'neighborhood': neighborhood,
    }

def generate_name(gender='random'):
    """Generate a realistic name."""
    if gender == 'random':
        gender = random.choice(['male', 'female'])

    first_name = random.choice(FIRST_NAMES.get(gender, FIRST_NAMES['male']))
    last_name = random.choice(LAST_NAMES)

    return {
        'first_name': first_name,
        'last_name': last_name,
        'full_name': f"{first_name} {last_name}",
        'gender': gender,
    }

def generate_rp_history():
    """Generate a realistic RP history."""
    histories = [
        "Moved to the city 5 years ago looking for a fresh start.",
        "Grew up in the neighborhood, knows everyone around here.",
        "Recently released from prison, trying to stay clean.",
        "Came from out of state to escape a troubled past.",
        "Local hustler with connections throughout the city.",
        "Working multiple jobs to support family.",
        "Involved with local gang since teenage years.",
        "Trying to go legitimate after years of street life.",
        "New to the city, still learning how things work.",
        "Long-time resident with deep roots in the community.",
        "Recently divorced, starting over from scratch.",
        "Inherited property in the area, now trying to manage it.",
        "Escaped abusive situation, building new life.",
        "Struggling with addiction, in recovery program.",
        "Successful business owner with questionable methods.",
    ]
    return random.choice(histories)

def generate_call_type():
    """Generate a random dispatch call type."""
    call_types = [
        'Accident', 'Assault', 'Burglary', 'Disturbance', 'Drug Activity',
        'Fraud', 'Gang Activity', 'Homicide', 'Robbery', 'Shooting',
        'Stolen Vehicle', 'Suspicious Activity', 'Traffic Stop', 'Welfare Check',
        'Domestic Violence', 'Trespassing', 'Vandalism', 'Weapons Violation',
        'DUI', 'Hit and Run', 'Noise Complaint', 'Parking Violation',
        'Loitering', 'Prostitution', 'Shoplifting', 'Assault with Weapon',
        'Armed Robbery', 'Drive-by Shooting', 'Gang Confrontation', 'Officer Assistance'
    ]
    return random.choice(call_types)

def generate_dispatch_call():
    """Generate a random dispatch call."""
    location = generate_address()
    call_type = generate_call_type()
    priorities = ['Low', 'Medium', 'High', 'Critical']
    priority = random.choice(priorities)

    descriptions = [
        f"Report of {call_type.lower()} in progress",
        f"Caller reports {call_type.lower()} at location",
        f"Possible {call_type.lower()} - units respond",
        f"Dispatch: {call_type} reported by civilian",
        f"Multiple callers reporting {call_type.lower()}",
    ]

    return {
        'location': location,
        'call_type': call_type,
        'priority': priority,
        'description': random.choice(descriptions),
    }
