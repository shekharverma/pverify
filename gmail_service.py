import imaplib
import email
import os

# IMAP Configuration
EMAIL = "d21780965@gmail.com"
PASSWORD = "vxie omdi lofi rjqq" 
IMAP_SERVER = "imap.gmail.com"

def download_imap_attachments():
    # Connect and login
    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(EMAIL, PASSWORD)
    mail.select("inbox")

    # Search for emails with attachments
    result, data = mail.search(None, '(X-GM-RAW "has:attachment filename:pdf")')
    
    for num in data[0].split():
        # Fetch the email body (RFC822)
        result, data = mail.fetch(num, '(RFC822)')
        raw_email = data[0][1]
        msg = email.message_from_bytes(raw_email)

        for part in msg.walk():
            if part.get_content_maintype() == 'multipart': continue
            if part.get('Content-Disposition') is None: continue
            
            filename = part.get_filename()
            if filename and filename.endswith('.pdf'):
                filepath = os.path.join('downloads', filename)
                with open(filepath, 'wb') as f:
                    f.write(part.get_payload(decode=True))
                print(f"Downloaded via IMAP: {filename}")

    mail.logout()



if __name__ == "__main__":
    # Ensure the downloads folder exists before running
    if not os.path.exists('downloads'):
        os.makedirs('downloads')
    
    print("Starting attachment download...")
    download_imap_attachments()
    print("Process complete.")