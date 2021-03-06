# Solar-Energy-Model

This is a basic model of solar panels and home battery that makes predictions on your electricity bill based on:

- Your annual consumption data in KwH and the profile of consumption per hour throughout the day
or
- Octopus Half Hourly Data downloaded from the Octopus web site
or
- Live access to the Octopus API

Instructions:

For the CSV method:
- Go to the Octopus energy web site and download your consumption over the last 12 months (or more)
- Save consumption.csv into the same directory as the python code

or for profile only method:

- Edit CONSUMPTION to set your annual useage in KwH
- If you want to create a PROFILE which is 24 numbers (one per hour starting at midnight) that gives the percentage usage in that hour. It doesn't need to add up to 100 as it will be scaled

or for direct API access:

- Login to your Octopus account and go to developer dashboard (via my account and API access use this link):
  https://octopus.energy/dashboard/developer/
- Copy the API key and save it into the .yml file as API_KEY: "xxx"
- Copy the MPAN and save it into the .yml file as API_MPAN: "xxx"
- Copy the Meter serial and save it into the .yml file as API_SERIAL: "xxx"

Configure your setup:

- Copy and edit test.yml to create your configuration 
  - Set the Solar generation and the Battery amount (use 0 if you don't plan to have these)
  - Take care of things like your inverter size (in kw) and also your batteries depth of discharge and expected degregration.
  - Configure your day and night rate (if there is no night rate just set it to the same price as day rate)
  - Change your expected inflation (increase of bills year on year)

Run:

- python3 solar.py my_setup.yml <mode>
  - Set mode to 'csv' if you want to read the CSV file, use 'api' for the API or 'profile' for the profiule data
- Review the output predictions, and also the created .csv data from the model
- You can override YML options using the command line e.g. --PRICE_DAY 0.35

