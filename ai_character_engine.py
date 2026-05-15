import os
import json
import random
import requests
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)
OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY')


def generate_character_prompt(age, gender, race, occupation_type, neighborhood):
    """Generate prompt for AI to create civilian with ONLY form-visible fields."""

    return f"""You are a GTA RP character designer. Generate a BRAND NEW CITY RESIDENT with a CLEAN RECORD.

PARAMETERS:
- Age: {age}
- Gender: {gender}
- Ethnicity: {race}
- Occupation: {occupation_type}
- Neighborhood: {neighborhood}

CRITICAL RULES:
1. This is a NEW RESIDENT - NO criminal history, NO warrants, NO arrests
2. Generate ONLY these fields (nothing else):
   - first_name: unique, realistic name (NEVER generic like John Doe)
   - last_name: realistic surname
   - date_of_birth: YYYY-MM-DD format
   - phone_number: 555-XXXX format
   - address: realistic GTA address in neighborhood
   - occupation: specific job title
   - gang_affiliation: "None" (always)
   - emergency_contact_name: realistic name
   - emergency_contact_phone: 555-XXXX format
   - driver_license_status: "Valid" (always)
   - firearm_license_status: "None" (always)
   - business_license_status: "None" (always)
   - vehicle_make: null (civilians visit dealerships in RP)
   - vehicle_model: null
   - vehicle_year: null
   - vehicle_color: null
   - plate_number: null
   - insurance_status: "Valid"
   - criminal_background_notes: "No criminal history on file"
   - character_backstory: 3-4 sentence RP backstory as new resident

Return ONLY valid JSON (no markdown, no code blocks):
{{
  "first_name": "unique first name",
  "last_name": "unique last name",
  "date_of_birth": "YYYY-MM-DD",
  "gender": "{gender}",
  "phone_number": "555-XXXX",
  "address": "realistic GTA address",
  "occupation": "{occupation_type}",
  "gang_affiliation": "None",
  "emergency_contact_name": "realistic name",
  "emergency_contact_phone": "555-XXXX",
  "driver_license_status": "Valid",
  "firearm_license_status": "None",
  "business_license_status": "None",
  "vehicle_make": null,
  "vehicle_model": null,
  "vehicle_year": null,
  "vehicle_color": null,
  "plate_number": null,
  "insurance_status": "Valid",
  "criminal_background_notes": "No criminal history on file",
  "character_backstory": "3-4 sentence backstory as new resident"
}}

Make this character FEEL REAL. New to the city, clean record, no vehicles yet."""


def generate_character(age=None, gender='random', race='random', occupation_type='random', neighborhood='random'):
    """Generate civilian with ONLY form-visible fields."""

    if not OPENROUTER_API_KEY:
        logger.error('OPENROUTER_API_KEY not configured')
        return {'error': 'AI service not configured'}

    # Default age if not provided
    if not age:
        age = random.randint(18, 70)

    prompt = generate_character_prompt(age, gender, race, occupation_type, neighborhood)

    try:
        response = requests.post(
            'https://openrouter.ai/api/v1/chat/completions',
            headers={
                'Authorization': f'Bearer {OPENROUTER_API_KEY}',
                'HTTP-Referer': 'http://localhost',
                'X-Title': 'NThaCityRP',
            },
            json={
                'model': 'openai/gpt-3.5-turbo',
                'messages': [{'role': 'user', 'content': prompt}],
                'temperature': 0.8,
                'max_tokens': 1500,
            },
            timeout=30,
        )

        if response.status_code == 200:
            result = response.json()
            if 'choices' in result and len(result['choices']) > 0:
                content = result['choices'][0]['message']['content'].strip()

                # Remove markdown code blocks if present
                if content.startswith('```'):
                    content = content.split('```')[1]
                    if content.startswith('json'):
                        content = content[4:]

                character_data = json.loads(content)

                # ENFORCE CLEAN RECORD - ONLY FORM FIELDS
                character_data['gang_affiliation'] = 'None'
                character_data['driver_license_status'] = 'Valid'
                character_data['firearm_license_status'] = 'None'
                character_data['business_license_status'] = 'None'
                character_data['vehicle_make'] = None
                character_data['vehicle_model'] = None
                character_data['vehicle_year'] = None
                character_data['vehicle_color'] = None
                character_data['plate_number'] = None
                character_data['insurance_status'] = 'Valid'
                character_data['criminal_background_notes'] = 'No criminal history on file'

                return character_data
            else:
                logger.error('No choices in API response')
                return {'error': 'No response from AI'}
        else:
            logger.error(f'API error: {response.status_code}')
            return {'error': f'API error: {response.status_code}'}

    except requests.exceptions.Timeout:
        logger.error('AI request timeout')
        return {'error': 'AI request timeout'}
    except json.JSONDecodeError as e:
        logger.error(f'Failed to parse AI response: {e}')
        return {'error': 'Invalid AI response format'}
    except Exception as e:
        logger.error(f'AI generation error: {e}')
        return {'error': str(e)}


def generate_narrative(narrative_type, context):
    """Generate AI narratives for reports."""

    if not OPENROUTER_API_KEY:
        return {'error': 'AI service not configured'}

    narrative_prompts = {
        'probable_cause': f"""Generate a professional, court-defensible probable cause statement.

Context: {context}

Return JSON:
{{
  "probable_cause": "detailed probable cause statement",
  "charges_justified": ["charge1", "charge2"],
  "evidence_summary": "summary of evidence"
}}""",

        'arrest_narrative': f"""Generate a professional arrest report narrative.

Context: {context}

Return JSON:
{{
  "narrative": "detailed arrest narrative",
  "charges": ["charge1", "charge2"],
  "summary": "one-paragraph summary"
}}""",

        'dispatch_summary': f"""Generate a dispatch summary for active call.

Context: {context}

Return JSON:
{{
  "dispatch_code": "10-XX code",
  "summary": "brief dispatch summary",
  "priority": "Low/Medium/High/Critical",
  "units_needed": ["unit type1", "unit type2"]
}}""",

        'witness_statement': f"""Generate a realistic witness statement.

Context: {context}

Return JSON:
{{
  "statement": "detailed witness account",
  "credibility": "High/Medium/Low",
  "key_details": ["detail1", "detail2"]
}}""",

        'use_of_force_narrative': f"""Generate a court-defensible use-of-force narrative.

Context: {context}

Return JSON:
{{
  "narrative": "detailed use-of-force narrative",
  "justification": "why force was necessary",
  "injuries": "injuries sustained",
  "force_type": "type of force used"
}}""",
    }

    prompt = narrative_prompts.get(narrative_type, narrative_prompts['arrest_narrative'])

    try:
        response = requests.post(
            f'{OPENROUTER_BASE_URL}/chat/completions',
            headers={
                'Authorization': f'Bearer {OPENROUTER_API_KEY}',
                'Content-Type': 'application/json',
                'HTTP-Referer': 'https://nthacityrp.com',
                'X-Title': 'NThaCityRP Police CAD',
            },
            json={
                'model': 'openrouter/auto',
                'messages': [
                    {'role': 'user', 'content': prompt}
                ],
                'temperature': 0.7,
                'max_tokens': 1000,
            },
            timeout=30
        )

        if response.status_code != 200:
            logger.error(f'OpenRouter API error: {response.status_code}')
            return {'error': f'API error: {response.status_code}'}

        data = response.json()
        content = data['choices'][0]['message']['content'].strip()

        try:
            if content.startswith('```'):
                content = content.split('```')[1]
                if content.startswith('json'):
                    content = content[4:]

            narrative_data = json.loads(content)
            return narrative_data
        except json.JSONDecodeError:
            logger.error(f'Failed to parse narrative response: {content[:200]}')
            return {'error': 'Failed to parse response'}

    except requests.RequestException as e:
        logger.error(f'OpenRouter request failed: {e}')
        return {'error': f'Request failed: {str(e)}'}
