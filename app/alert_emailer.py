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
        # Default action recommendations
        recommendations = ""

        if "NAV has dropped" in body:
            recommendations += "\n\n🛑 Recommendation: Review fund health. Significant NAV decline could signal asset weakness. Consider reducing exposure if trend persists."

        if "Volume dropped" in body:
            recommendations += "\n\n⚠️ Recommendation: Monitor liquidity. Falling volume could make selling harder or indicate falling investor interest."

        if "Premium/Discount exceeds" in body:
            recommendations += "\n\n⚡ Recommendation: Verify market mispricing. Large deviations may signal trading opportunities or hidden risk."

        full_body = body + recommendations

        msg = MIMEMultipart()
        msg['From'] = EMAIL_SETTINGS['sender_email']
        msg['To'] = ", ".join(EMAIL_SETTINGS['receivers'])
        msg['Subject'] = subject
        msg.attach(MIMEText(full_body, 'plain'))

        server = smtplib.SMTP(EMAIL_SETTINGS['smtp_server'], EMAIL_SETTINGS['smtp_port'])
        server.starttls()
        server.login(EMAIL_SETTINGS['sender_email'], EMAIL_SETTINGS['sender_password'])
        server.sendmail(EMAIL_SETTINGS['sender_email'], EMAIL_SETTINGS['receivers'], msg.as_string())
        server.quit()

        print(f"✅ Alert email sent: {subject}")
    except Exception as e:
        print(f"Error sending email alert: {e}")

