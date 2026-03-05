import os
import json
import re
import logging
import mimetypes
from google import genai
from google.genai import types
from dotenv import load_dotenv

# --- SETUP LOGGING FOR THIS MODULE ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
        if "```" in text:
            text = re.sub(r"```json\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"```", "", text)
        
        match = re.search(r'(\{.*\}|\[.*\])', text, re.DOTALL)
        if match:
            text = match.group(0)
            
        return text.strip()
    except Exception as e:
        logger.error(f"Error cleaning JSON text: {e}")
        return text

def get_mime_type(file_path):
    mime_type, _ = mimetypes.guess_type(file_path)
    return mime_type or "application/pdf"

def extract_from_referral(pdf_path):
    try:
        if not os.path.exists(pdf_path):
            logger.error(f"File not found: {pdf_path}")
            return {"error": "File path does not exist"}

        logger.info(f"📤 Uploading file to Gemini: {os.path.basename(pdf_path)}")
        
        mime_type = get_mime_type(pdf_path)
        
        medical_doc = client.files.upload(
            file=pdf_path,
            config={'mime_type': mime_type} 
        )

        prompt = """
        You are an expert medical data extraction AI. Analyze the attached document.
        
        TASK:
        Extract patient demographics, PRIMARY insurance, and SECONDARY insurance.
        
        GUIDELINES:
        1. **Primary vs Secondary**: Look for labels like "Primary Insurance" vs "Secondary Insurance". If not explicitly labeled, Medicare is usually Primary.
        2. **Member IDs**: Extract the "Subscriber ID" or "Member ID". Remove spaces/dashes.
        3. **Payer Names**: Extract the full insurance name (e.g. "Medicare Part B", "AARP UnitedHealthcare").
        4. **Date of Birth**: Format as MM/DD/YYYY.
        5. **Missing Data**: If Secondary insurance is not found, leave those fields empty. DO NOT extract handwriting.

        REQUIRED JSON STRUCTURE:
        {
          "form_population_data": {
            "first_name": "String",
            "last_name": "String",
            "dob": "MM/DD/YYYY",
            "primary": {
                "member_id": "String",
                "payer_name": "String"
            },
            "secondary": {
                "member_id": "String",
                "payer_name": "String"
            }
          }
        }
        """

        logger.info("🤖 Sending prompt to Gemini...")
        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=[medical_doc, prompt],
            config=types.GenerateContentConfig(
                response_mime_type='application/json',
                temperature=0.1 
            )
        )

        try:
            client.files.delete(name=medical_doc.name)
        except Exception as e:
            logger.warning(f"Failed to delete remote file: {e}")
        
        logger.info(f"📝 Gemini Raw Response: {response.text}")

        cleaned_text = clean_json_text(response.text)
        
        try:
            data = json.loads(cleaned_text)
            return data

        except json.JSONDecodeError as je:
            logger.error(f"❌ JSON Decode Error: {je}")
            try:
                fixed_text = cleaned_text.replace("'", '"')
                return json.loads(fixed_text)
            except:
                return {"error": "AI returned invalid JSON format."}

    except Exception as e:
        logger.exception(f"🔥 Critical Extraction Failure: {str(e)}")
        return {"error": f"Extraction Failed: {str(e)}"}