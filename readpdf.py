import os
import json
from google import genai
from google.genai import types
from dotenv import load_dotenv  # <--- Add this

load_dotenv()
api_key = os.environ.get("GEMINI_API_KEY")

client = genai.Client(
    api_key=api_key,
    http_options={'api_version': 'v1beta'}
)

# In readpdf.py

def extract_from_referral(pdf_path):
    try:
        if not os.path.exists(pdf_path):
            return {"error": "File path does not exist"}

        medical_doc = client.files.upload(file=pdf_path)

        # 1. STRICT PROMPT (Enforces keys and forbids nulls)
        prompt = """
        You are a medical data extraction API. Your job is to extract data into a strict JSON format.
        
        RULES:
        1. Extract specific form data into 'form_population_data'.
        2. Extract full details into 'insurance_details'.
        3. IF A VALUE IS MISSING, RETURN AN EMPTY STRING "". DO NOT RETURN null OR None.
        4. Do not invent data. Use the text exactly as it appears.

        REQUIRED JSON STRUCTURE:
        {
          "form_population_data": {
            "first_name": "Patient first name",
            "last_name": "Patient last name",
            "dob": "MM/DD/YYYY",
            "member_id": "Insurance Member ID / Subscriber ID",
            "payer_name": "Insurance Company Name (e.g. BCBS, Aetna)"
          },
          "insurance_details": {
             ... extract all other clinical and provider data here ...
          }
        }
        """

        # 2. CONFIGURATION (The Magic Fix)
        # temperature=0.0 removes randomness.
        # response_mime_type='application/json' enforces JSON.
        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=[medical_doc, prompt],
            config=types.GenerateContentConfig(
                response_mime_type='application/json',
                temperature=0.0  # <--- THIS IS CRITICAL FOR CONSISTENCY
            )
        )

        client.files.delete(name=medical_doc.name)
        
        # Cleanup markdown formatting if present
        raw_text = response.text.strip()
        if raw_text.startswith("```"):
             raw_text = raw_text.split("\n", 1)[1].rsplit("\n", 1)[0].strip()

        return json.loads(raw_text)

    except Exception as e:
        return {"error": str(e)}
if __name__ == "__main__":
    file_path = "lefevre,brenda_referral 5.8.24.pdf"
    data = extract_from_referral(file_path)
    if data:
        print(json.dumps(data, indent=4))