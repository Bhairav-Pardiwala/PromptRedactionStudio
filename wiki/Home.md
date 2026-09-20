# Prompt Redaction Studio

Personal data has a habit of ending up in prompts. A customer's name, their email, the card
number they quoted, the address on the complaint — you paste the whole thing into a chatbot
because that is the fastest way to get an answer, and the data goes with it.

This app sits in between. You give it your text, it finds the personal data and swaps each
value for a numbered token, and you send the safe version to the model instead. When the
model answers, you paste the reply back and the real values return.

![The web UI detecting personal data in a prompt, with the options panel on the left and scored findings on the right](https://raw.githubusercontent.com/Bhairav-Pardiwala/PromptRedactionStudio/main/docs/web-ui-detection.png)

Everything happens on your own machine. No AI model is called, nothing is written to disk,
and nothing you paste leaves the box.

## Start here

| If you… | Go to |
|---|---|
| Just want to get it running | **[Installing](Installing)** |
| Have it open in a browser | **[Using the Web App](Using-the-Web-App)** |
| Were given a URL and a desktop app by your IT team | **[Using the Desktop App](Using-the-Desktop-App)** |
| Want to know what it finds, and what it misses | **[What Gets Detected](What-Gets-Detected)** |
| Want to change how values are replaced | **[Redaction Options](Redaction-Options)** |

## The two questions everyone asks

- **Is my data safe?** → [Privacy and Data Handling](Privacy-and-Data-Handling)
- **Something isn't working.** → [Troubleshooting](Troubleshooting) · [FAQ](FAQ)

> Every name, card number and address shown in this wiki, in the app's sample prompt and in
> the screenshots is **made up**. `4111 1111 1111 1111` is the standard Visa test number and
> `example.com` is a reserved documentation domain. No real personal data appears anywhere
> in this project.
