import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import yaml

# Load config
with open('config.yaml', 'r') as file:
    config = yaml.safe_load(file)

EMAIL_SETTINGS = config['email_settings']

def send_email_alert(subject, body):
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_SETTINGS['sender_email']
        msg['To'] = ", ".join(EMAIL_SETTINGS['receivers'])
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        server = smtplib.SMTP(EMAIL_SETTINGS['smtp_server'], EMAIL_SETTINGS['smtp_port'])
        server.starttls()
        server.login(EMAIL_SETTINGS['sender_email'], EMAIL_SETTINGS['sender_password'])
        server.sendmail(EMAIL_SETTINGS['sender_email'], EMAIL_SETTINGS['receivers'], msg.as_string())
        server.quit()

        print(f"✅ Alert email sent: {subject}")
    except Exception as e:
        print(f"❌ Error sending alert email: {e}")

