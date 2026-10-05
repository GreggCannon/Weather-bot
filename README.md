# Weather Email Bot

Emails you the current weather for your chosen cities every 30 minutes.
It runs for free on GitHub's servers, so your computer can be off.

## Setup (about 10 minutes, one time)

### 1. Get a Gmail App Password (the bot's "sending" account)
1. Use a Gmail account (a personal one is easiest).
2. Turn on 2-Step Verification: https://myaccount.google.com/security
3. Create an App Password: https://myaccount.google.com/apppasswords
   Name it "Weather Bot" and copy the 16-character password.

### 2. Put the bot on GitHub
1. Create a free account at https://github.com if you don't have one.
2. Click **New repository**, name it `weather-bot`, choose **Private**, click **Create**.
3. Click **uploading an existing file** and drag in everything from this folder,
   including the `.github` folder. (If the `.github` folder won't upload, use
   **Add file → Create new file**, type `.github/workflows/weather.yml` as the
   name, and paste in the contents of that file.)
4. Click **Commit changes**.

### 3. Add your secrets
In the repository: **Settings → Secrets and variables → Actions → New repository secret**.
Add these three:

| Name             | Value                                             |
|------------------|---------------------------------------------------|
| `EMAIL_USER`     | the Gmail address from step 1                     |
| `EMAIL_PASSWORD` | the 16-character App Password (no spaces)         |
| `EMAIL_TO`       | where the weather should go (comma-separate several) |

### 4. Test it
Go to the **Actions** tab → **Weather Email** → **Run workflow**.
You should get an email within a minute. After that it runs every 30 minutes on its own.

## Changing cities
Edit `cities.txt` on GitHub (click the file, then the pencil icon). One city per line,
e.g. `Navasota, TX` or `Paris, France`. Save, and the next email uses the new list.

## Changing other things
- **Celsius instead of Fahrenheit:** in `.github/workflows/weather.yml`, change `UNITS: imperial` to `UNITS: metric`.
- **Different schedule:** change the `cron` line. Examples: `0 * * * *` = hourly,
  `0 12 * * *` = once a day at 7am Central (times are UTC).
- **Pause it:** Actions tab → Weather Email → "..." menu → **Disable workflow**.

## Good to know
- GitHub sometimes runs scheduled jobs a few minutes late during busy periods.
- GitHub pauses scheduled jobs if a repository has no activity for 60 days. If the
  emails stop, open the Actions tab and click **Enable workflow** (or edit any file).
- Weather data comes from Open-Meteo.com — free, no account or API key needed.
