# Solar-Energy-Model

This is a basic model of solar panels and home battery that makes predictions on your electricity bill based on Octopus Half Hourly Data or you can use a percentage per hour profile instead.

Instructions:

- Go to the Octopus energy web site and download your consumption over the last 12 months (or more)
- Save consumption.csv into the same directory as the python code

or

- Comment out the line in the configuration that says consumption.csv and edit the PROFILE and ANNUAL_USAGE data instead

- Copy and edit test.yml to create your configuration
- run solar.py test.yml
- Review the output predictions, and also the created .csv data from the model
