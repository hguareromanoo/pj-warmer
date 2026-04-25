import os
import requests
from dotenv import load_dotenv

load_dotenv()

PIPEDRIVE_API_KEY = os.getenv('PIPEDRIVE_API_KEY')
BASE_URL = "https://polijunior.pipedrive.com/api/v1"

def _get(endpoint, params=None):
    if params is None:
        params = {}
    params['api_token'] = PIPEDRIVE_API_KEY
    response = requests.get(f"{BASE_URL}/{endpoint}", params=params)
    response.raise_for_status()
    return response.json().get('data')

def get_users():
    """Fetch all users (owners)."""
    data = _get("users")
    return data if data else []

def get_organizations():
    """Fetch all organizations."""
    data = _get("organizations")
    return data if data else []

def get_organization(org_id):
    """Fetch specific organization by ID."""
    data = _get(f"organizations/{org_id}")
    return data

def search_deals(query, search_mode="Título do card"):
    """
    Search for deals via Pipedrive itemSearch API.
    Uses 'deal' item type natively. If searching by organization,
    we must search for organizations first, but Pipedrive's itemSearch
    searches multiple fields. We'll rely on item_type='deal'.
    """
    if not query:
        return []
        
    params = {
        "term": query,
        "item_types": "deal",
        "limit": 50,
        "exact_match": 0
    }
    
    # According to pipedrive docs: /v1/itemSearch
    data = _get("itemSearch", params=params)
    
    if not data or not data.get("items"):
        return []
        
    results = []
    # itemSearch returns a slightly different structure:
    # { 'items': [ {'item': {'id': 1, 'title': '...', 'organization': {'id': 2, 'name': '...'}}} ] }
    # To keep compatibility with our other endpoints (and custom fields), we must fetch the full deal details
    # for the matching IDs, because itemSearch doesn't return custom fields like link_drive or person_id fully.
    # Alternatively, we just return the basic IDs and fetch the full deal ONLY when the user clicks 'Abrir Card'. 
    # That is much more efficient.
    
    for item_wrapper in data.get("items", []):
        deal_item = item_wrapper.get("item", {})
        results.append({
            "id": deal_item.get("id"),
            "title": deal_item.get("title"),
            "org_id": deal_item.get("organization"), # structure: {id, name, address}
            "person_id": deal_item.get("person"),
            "user_id": deal_item.get("owner"), # API might return 'owner' instead of 'user_id' in itemSearch
        })
        
    return results

def get_deal(deal_id):
    """Fetch full deal details, since itemSearch doesn't return custom fields."""
    return _get(f"deals/{deal_id}")

def get_person(person_id):
    """Fetch person by ID."""
    data = _get(f"persons/{person_id}")
    return data

def get_deal_notes(deal_id):
    """Fetch all notes for a deal."""
    data = _get("notes", params={"deal_id": deal_id})
    return data if data else []

def add_deal_note(deal_id, content):
    """Adds a new note to a specific deal."""
    params = {'api_token': PIPEDRIVE_API_KEY}
    payload = {
        "deal_id": deal_id,
        "content": content
    }
    response = requests.post(f"{BASE_URL}/notes", params=params, json=payload)
    response.raise_for_status()
    return response.json().get('data')
