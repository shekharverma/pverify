import os
import json
import re
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()
api_key = os.environ.get("GEMINI_API_KEY")

client = genai.Client(
    api_key=api_key,
    http_options={'api_version': 'v1beta'}
)

def clean_json_text(text):
    """
    Scans the text for the first JSON object {} or array [] and returns it.
    Removes Markdown code blocks.
    """
    try:
        # 1. Remove Markdown code blocks
        if "```" in text:
            text = re.sub(r"```json\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"```", "", text)
        
        # 2. Find the first '{' and the last '}' (or brackets)
        match = re.search(r'(\{.*\}|\[.*\])', text, re.DOTALL)
        if match:
            text = match.group(0)
            
        return text.strip()
    except Exception:
        return text

def extract_from_referral(pdf_path):
    try:
        if not os.path.exists(pdf_path):
            return {"error": "File path does not exist"}

        medical_doc = client.files.upload(file=pdf_path)

        prompt = """
        You are a medical data extraction API. Extract data into this strict JSON format.
        
        REQUIRED JSON STRUCTURE:
        {
          "form_population_data": {
            "first_name": "Patient First Name",
            "last_name": "Patient Last Name",
            "dob": "MM/DD/YYYY",
            "member_id": "Member ID",
            "payer_name": "Payer Name"
          }
        }
        """

        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=[medical_doc, prompt],
            config=types.GenerateContentConfig(
                response_mime_type='application/json',
                temperature=0.0 
            )
        )

        client.files.delete(name=medical_doc.name)
        
        # CLEAN AND PARSE
        cleaned_text = clean_json_text(response.text)
        
        try:
            return json.loads(cleaned_text)
        except json.JSONDecodeError:
            # Fallback: Try to fix single quotes which is a common AI error
            try:
                fixed_text = cleaned_text.replace("'", '"')
                return json.loads(fixed_text)
            except:
                return {"error": "AI returned invalid JSON format."}

    except Exception as e:
        return {"error": f"Extraction Failed: {str(e)}"}

if __name__ == "__main__":
    pass