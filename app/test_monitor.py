from alert_emailer import send_email_alert

# Test Subject and Message
subject = "✅ TEST: ETF Monitor Alert System Working"
body = """
This is a TEST ALERT.

If you received this email and/or SMS, your ETF Monitor notification system is correctly configured!

- Test from ETF Monitor Bot 🚀
"""

# Send the alert
send_email_alert(subject=subject, body=body)

