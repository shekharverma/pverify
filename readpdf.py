import os
import json
from google import genai
from google.genai import types

client = genai.Client(
    api_key="AIzaSyBHOXym8COoMrDxE9GSDXvDw4x7A37yPyQ",
    http_options={'api_version': 'v1beta'}
)

def extract_from_referral(pdf_path):
    try:
        if not os.path.exists(pdf_path):
            return None

        # Upload the PDF
        medical_doc = client.files.upload(file=pdf_path)

        # Prompt modified to allow the AI to build the best structure it can
        prompt = """
        Extract EVERY detail from this document into a structured JSON format. 
        Create a single top-level key named 'insurance_details'.
        Inside 'insurance_details', create sub-keys for:
        - Patient demographics
        - Insurance info (Member ID, Payer, Group)
        - Provider info (NPI, Name, Facility)
        - All clinical codes and notes
        
        Capture every bit of text. Do not leave anything behind.
        """

        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=[medical_doc, prompt],
            config=types.GenerateContentConfig(
                # We use 'application/json' but REMOVE the rigid response_schema
                # This enables "JSON Mode" for maximum flexibility
                response_mime_type='application/json'
            )
        )

        client.files.delete(name=medical_doc.name)
        
        # Parse the dynamic JSON directly
        return json.loads(response.text)

    except json.JSONDecodeError as je:
        print(f"JSON Parsing Error: The model output invalid JSON. Details: {je}")
        # Fallback: Print raw text to see what happened
        print("Raw Response:", response.text)
        return None
    except Exception as e:
        print(f"Extraction failed: {str(e)}")
        return None

if __name__ == "__main__":
    file_path = "lefevre,brenda_referral 5.8.24.pdf"
    data = extract_from_referral(file_path)
    if data:
        print(json.dumps(data, indent=4))