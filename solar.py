#
# Solar and battery model based on the use of Octopus Energy consumption data
#
# Copyright Trefor Southwell - 2022 - trefor@tdlj.net
#
# No warranty is given for the accuracy of predictions made by this model or the real savings that can be achieved
#
from datetime import datetime
from datetime import timedelta
import math
import yaml
import argparse
import sys
import urllib.request
import json
import re

# Default configuration - overriden by YML
CONFIG = {
    'NIGHT_START' : 1,
    'NIGHT_END' : 5,
    'BATTERY_SIZE' : 0,
    'BATTERY_LOSS' : 0.96,
    'BATTERY_DOD'  : 0.90,
    'BATTERY_PEAK_DRAW' : 3.0,
    'BATTERY_MAX_CHARGE_RATE' : 3.0,
    'BATTERY_CHARGE_NIGHT' : True,
    'BATTERY_GROW' : 0,
    'BATTERY_GROW_COST' : 1000,
    'BATTERY_MAX' : 18,
    'SOLAR_SIZE' : 0,
    'SOLAR_YIELD' : 1.0,
    'PRICE_DAY' : 0.30,
    'PRICE_NIGHT' : 0.075,
    'PRICE_FEEDIN' : 0.04,
    'DYNAMIC_CHARGE' : 0,
    'ANNUAL_BATTERY_LOSS' : 0.984,
    'INFLATION' : 1.03,
    'EQUIPMENT_COST' : 0,
    'YEARS' : 15,
    'PROFILE' : [1,1,1,1,1,1,2,5,5,5,4,4,7,5,3,2,3,4,5,5,4,4,4,2],
    'CONSUMPTION' : "consumption.csv",
    'ANNUAL_USAGE': 6000.0,
    'SUNRISE': "sunrise.txt",
    'API_KEY' : None,
    'API_MPAN' : None,
    'API_SERIAL' : None,
    'API_CONSUMPTION' : "https://api.octopus.energy/v1/electricity-meter-points/%s/meters/%s/consumption/?page_size=20000",
}

class cl_logger:
    def __init__(self, filename):
        self.han = open(filename, 'w')
        self.han.write("mode, day, hour, load, solar_produce, charge_battery, draw_grid, battery_level\n")
    
    def row(self, mode, day, hour, load, produce, charge, grid, battery):
        self.han.write("%s, %d, %d, %f, %f, %f, %f, %f\n" % (mode, day, hour, load, produce, charge, grid, battery))

class cl_battery:
    """ Battery model """
    def __init__(self, cap, loss, dod):
        self.charge = 0
        self.max = cap * dod
        self.loss = loss
        self.charge_in = 0
        self.charge_out = 0
        self.target_charge_level = self.max
    
    def hour(self, hour):
        # store last nights charge level
        if (hour == 0):
           self.last_charge_level = self.charge

           if CONFIG['DYNAMIC_CHARGE']:
               if (self.last_charge_level < 0.5):
                   self.target_charge_level = min(self.max, self.target_charge_level + 0.2)
               if (self.last_charge_level > 1.0):
                   self.target_charge_level = max(0, self.target_charge_level - 0.2)

    def do_charge(self, kw):

        charge_amount = kw * self.loss
        if charge_amount + self.charge > self.max:
            charge_amount = self.max - self.charge

        # Left over energy doesn't have losses in battery
        kw = kw - (charge_amount / self.loss)
            
        self.charge    += charge_amount
        self.charge_in += charge_amount

        return kw

    def draw(self, kw):
        # print "battery draw from %f %f" % (self.charge, kw)
        drawn = min(self.charge, kw)
        drawn = min(drawn, CONFIG['BATTERY_PEAK_DRAW'])
        self.charge -= drawn
        self.charge_out += drawn
        return kw - drawn

    def can_charge(self):
        # Base on yesterdays performance lets give some margin but try to target zero battery at midnight
        recommended = max(self.target_charge_level - self.charge, 0)
        return recommended / self.loss

    def show(self):
        print ("Battery is at %f kw / %f max" % (self.charge, self.max))
        print ("Battery incoming energy %f kw outgoing %f kw" % (self.charge_in, self.charge_out))

class cl_panels:
    """ Solar panel model """
    def __init__(self, size, efficiency):
        self.size = size
        self.producing = 0
        self.total_produced = 0
        self.efficiency = efficiency
        
    def energy(self, hours):
        energy = self.size * hours * self.efficiency
        self.total_produced += energy
        self.producing = energy
        return energy
    
    def show(self):
        print ("Panel size %f produced %f kw\n" % (self.size, self.total_produced))
        
class cl_sun:
    """ Sun model """
    def hours(self, day, hour):
        rise = self.rise[day]
        fall = self.fall[day]
        hours_per_day = self.sun_hours_per_day[rise.month - 1]

        if hour < rise.hour:
            return 0
        if hour >= fall.hour:
            return 0

        # seconds = (fall - rise).total_seconds()
        hours = fall.hour - rise.hour + 1 # seconds / (60.0 * 60.0)
        hour_offset = hour - rise.hour + 0.5

        # place in curve
        place = math.sin(3.141 * hour_offset / hours) * 1.5

        hours_per_hour = hours_per_day / float(hours) * place
        return hours_per_hour
        
    def __init__(self, sunrise):
        self.sun_hours_per_day = [1.8, 2.7, 5.2, 7.8, 9.7, 6.2, 5.6, 5.2, 5.4, 2.2, 2.1, 1.6]
        self.rise = {}
        self.fall = {}
        with open(sunrise, 'r') as han:
            day = 1
            for line in han:
                if line:
                    rise, set = line.split()
                    rise_t = datetime.strptime(rise, '%H:%M:%S')
                    fall_t = datetime.strptime(set, '%H:%M:%S')
                    rise_t += timedelta(days=day)
                    fall_t += timedelta(days=day)
                    self.rise[day] = rise_t
                    self.fall[day] = fall_t
                    day += 1

class cl_grid:
    """ Grid model """
    def __init__(self):
        self.total_drawn = 0
        self.draw_day = 0
        self.draw_night = 0
        self.draw_feedin = 0
        self.cost = 0
        self.cost_day = 0
        self.cost_night = 0
        self.cost_feedin = 0
        self.price_day = CONFIG['PRICE_DAY']
        self.price_night = CONFIG['PRICE_NIGHT']
        self.price_feedin = CONFIG['PRICE_FEEDIN']

    def draw(self, load, hour):
        if load > 0:
            self.total_drawn += load
            if (hour >= CONFIG['NIGHT_START'] and hour < CONFIG['NIGHT_END']):
                self.cost += self.price_night * load
                self.cost_night += self.price_night * load
                self.draw_night += load
            else:
                self.cost_day += self.price_day * load
                self.cost += self.price_day * load
                self.draw_day += load
        else:
            self.cost += self.price_feedin * load
            self.cost_feedin += self.price_feedin * load 
            self.draw_feedin += load

    def show(self):
        print ("Grid has drawn %lf kw (day %lf kwh, night %lf kwh, feedin %lf kwh)" % (self.total_drawn, self.draw_day, self.draw_night, self.draw_feedin))
        print ("Grid has cost  %lf    (day rate %lf night %lf      feedin %lf )" % (self.cost, self.cost_day, self.cost_night, self.cost_feedin))
        
class cl_load:
    """ Load model """

    def load(self, kw):
        self.total_used += kw
    
    def create_profile(self, profile, total):

        profile_sum = 0.0
        for hour in range(24):
            profile_sum += profile[hour]
        for hour in range(24):
            profile[hour] = profile[hour] / profile_sum * 100.0

        for day in range(1, 365+1):
            self.data[day] = {}
            for hour in range(24):
                usage = profile[hour] * total / 100 / 365
                self.data[day][hour] = usage

    def load_csv(self, filename):
        results = []
        with open(filename, 'r') as han:
            last_hour = -1
            for line in han:
                if line:
                    line = line.strip()
                    fields = line.split(',')
                    if not fields[0].startswith('Consumption'):
                        point = {}
                        point['consumption'] = float(fields[0])
                        point['interval_start'] = fields[1]
                        point['interval_end'] = fields[2]
                        results.append(point)
        return results

    def process_results(self, results):
        """
        Change octoput results into data points
        """
        for result in results:
            istart = result['interval_start']
            iend   = result['interval_end']
            energy = result['consumption']

            start_date, start_time = istart.split('T')
            end_date, end_time = iend.split('T')
            start_time, offset_time = re.split('\+|Z', start_time)
            end_time, offset_end_time = re.split('\+|Z', end_time)
            start = datetime.strptime(start_date.strip() + " " + start_time, '%Y-%m-%d %H:%M:%S')
            end   = datetime.strptime(end_date.strip()   + " " + end_time,   '%Y-%m-%d %H:%M:%S')

            day_of_year = start.timetuple().tm_yday
            hour_of_day_start = start.hour
            hour_of_day_end = end.hour
            hours = hour_of_day_end - hour_of_day_start
            if (hours == 0):
                hours = 1

            for hour in range(hour_of_day_start, hour_of_day_start + hours):
                if day_of_year not in self.data:
                    self.data[day_of_year] = {}
                if hour not in self.data[day_of_year]:
                    self.data[day_of_year][hour] = energy / hours
                elif last_hour == hour_of_day_start:
                    self.data[day_of_year][hour] += energy / hours
                else:
                    # If the data covers multiple years use the latest only
                    self.data[day_of_year][hour] = energy / hours

                last_hour = hour_of_day_start
    
    def validate_data(self, show):
        self.hourly = [0 for i in range(24)]

        for day in range(1, 365 + 1):
            for hour in range(24):
                if day not in self.data:
                    print("ERROR: Input data is incomplete for day %d" % day)
                    exit(1)
                if hour not in self.data[day]:
                    print("ERROR: Input data is incomplete for day %d hour %d" % (day, hour))
                    exit(1)

                # Count per hour
                self.hourly[hour] += self.data[day][hour]
        
        # Create hourly profile
        self.hourly_profile = [0 for i in range(24)]
        total = sum(self.hourly)
        for hour in range(24):
            self.hourly_profile[hour] = self.hourly[hour] / total
        
        # Show profile
        if show:
            print("Total annual energy use: %0.2f kWh hourly profile:  " % total)
            print("    ", end='')
            for hour in range(24):
                vstr = "%0.2f, " % (self.hourly_profile[hour] * 100.0)
                print(vstr, end="")
            print()

    def reset(self):
        self.total_used = 0

    def __init__(self, filename, show, profile=None, total=3000.0, apimode=False):
        self.data = {}
        self.reset()
        
        if apimode:
            self.process_results(self.load_api())
        elif filename:
            self.process_results(self.load_csv(filename))
        else:
            self.create_profile(profile, total)
        self.validate_data(show)
        
                            
    def get_load(self, day, hour):
        if day in self.data:
            if hour in self.data[day]:
                if self.data[day][hour]:
                    return self.data[day][hour]
        return 0

    def show(self):
        print ("Total energy load used %lf kWh" % self.total_used)

    def set_api(self, api):
        print ("Login to api %s" % api)
        uname = CONFIG['API_KEY']
        password_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        password_mgr.add_password(None, api, uname, '')
        handler = urllib.request.HTTPBasicAuthHandler(password_mgr)
        opener = urllib.request.build_opener(handler)
        opener.open(api)
        urllib.request.install_opener(opener)

    def load_api(self, maxpoints=365*24*2):
        """
        Fetch consumption data from Octopus API
        """
        results = []

        if (not CONFIG['API_KEY']) or (not CONFIG['API_MPAN']) or (not CONFIG['API_SERIAL']):
            print("ERROR: You must set API_KEY, API_MPAN and API_SERIAL to load from Octopus API")
            exit(1)

        api = CONFIG['API_CONSUMPTION'] % (CONFIG['API_MPAN'], CONFIG['API_SERIAL'])
        self.set_api(api)

        while api and len(results) < maxpoints:
            print("Fetching %s" % api)
            with urllib.request.urlopen(api) as url:
                tdata = url.read()
                data = json.loads(tdata)
                if 'results' in data:
                    results += data['results']
                if 'next' in data:
                    api = data['next']
                else:
                    api = None
        print("Downloaded %d data points" % len(results))
        return results

def run_scenario(show, load):
    if show:
        print ("---------- BATTERY %f SOLAR %f COST %0.2f--------" % (CONFIG['BATTERY_SIZE'], CONFIG['SOLAR_SIZE'], CONFIG['EQUIPMENT_COST']))

    if show:
        log = cl_logger("data_bat%f_sol%f.csv"  % (CONFIG['BATTERY_SIZE'], CONFIG['SOLAR_SIZE']))
    else:
        log = None

    # Battery in kw and loss %
    battery = cl_battery(CONFIG['BATTERY_SIZE'], CONFIG['BATTERY_LOSS'], CONFIG['BATTERY_DOD'])

    # Panel in kw and loss %
    panel = cl_panels(CONFIG['SOLAR_SIZE'], 0.627 * CONFIG['SOLAR_YIELD'])

    # Sunrise data
    sun = cl_sun(CONFIG['SUNRISE'])

    # Reset load data
    load.reset()

    # Create grid
    grid = cl_grid()

    # One year
    day = 1
    while (day <= 365):
        hour = 0
        while (hour < 24):
            hours = sun.hours(day, hour)
            solar_energy = panel.energy(hours)
            battery.hour(hour)
        
            use = load.get_load(day, hour)
            load.load(use)
        
            spare_energy = solar_energy - use
            if spare_energy > 0:
              # Charge battery?
              left_over_energy = battery.do_charge(spare_energy)
              # Feed in?
              if left_over_energy > 0:
                  grid.draw(-left_over_energy, hour)        
              if show:      
                  log.row("Spare", day, hour, use, solar_energy, spare_energy - left_over_energy, -left_over_energy, battery.charge)
            else:
                # Charge battery on cheap rate?
                if hour >= CONFIG['NIGHT_START'] and hour <= CONFIG['NIGHT_END'] and CONFIG['BATTERY_CHARGE_NIGHT']:
                    to_battery = min(battery.can_charge(), CONFIG['BATTERY_MAX_CHARGE_RATE']) # max charge rate
                    grid.draw(to_battery - spare_energy, hour)
                    battery.do_charge(to_battery)
                    if show:      
                        log.row("Night", day, hour, use, solar_energy, to_battery, to_battery - spare_energy, battery.charge)
                else:
                    if hour >= CONFIG['NIGHT_START'] and hour <= CONFIG['NIGHT_END']:
                        # draw from grid
                        balance_energy = -spare_energy
                    else:
                        # Draw from battery
                        balance_energy = battery.draw(-spare_energy)
                    if balance_energy > 0:
                        # Buy from grid?
                        grid.draw(balance_energy, hour)
                    if show:      
                        log.row("Day", day, hour, use, solar_energy, balance_energy + spare_energy, balance_energy, battery.charge)
                
            hour += 1
        day += 1

    if show:
        load.show()
        panel.show()
        battery.show()
        grid.show()

    return grid.cost

def simulate(mode):
    total_cost = 0
    base_cost = 0
    year = 0

    # Octopus data or profiled load?
    if mode.lower() == 'api':
        load = cl_load(None, True, apimode=True)
    elif mode.lower() == 'csv':
        load = cl_load(CONFIG['CONSUMPTION'], True)
    else:
        load = cl_load(None, True, profile=CONFIG['PROFILE'], total=CONFIG['ANNUAL_USAGE'])

    while year < CONFIG['YEARS']:
        tempb = CONFIG['BATTERY_SIZE']
        temps = CONFIG['SOLAR_SIZE']
        CONFIG['BATTERY_SIZE'] = 0
        CONFIG['SOLAR_SIZE'] = 0

        base_cost_year = run_scenario(False, load=load)
        base_cost += base_cost_year

        CONFIG['BATTERY_SIZE'] = tempb
        CONFIG['SOLAR_SIZE'] = temps
        annual_cost = run_scenario(year==0, load=load)
        total_cost += annual_cost
        year += 1

        print("Year %2d - Predicted cost (rates day %0.2f night %0.2f): %0.2f base cost %0.2f saving %0.2f - Total saving: %0.2f" % (year, CONFIG['PRICE_DAY'], CONFIG['PRICE_NIGHT'], annual_cost, base_cost_year, base_cost_year - annual_cost, base_cost - total_cost - CONFIG['EQUIPMENT_COST']))

        # Annual adjustments
        CONFIG['BATTERY_SIZE'] *= CONFIG['ANNUAL_BATTERY_LOSS'] # Loss of battery capacity
        CONFIG['PRICE_DAY']    *= CONFIG['INFLATION'] # Inflation for electric costs
        CONFIG['PRICE_NIGHT']  *= CONFIG['INFLATION'] # Inflation for electric costs

        # Add batteries annually
        if (CONFIG['BATTERY_GROW'] and (CONFIG['BATTERY_SIZE'] + CONFIG['BATTERY_GROW']) <= CONFIG['BATTERY_MAX']):
            CONFIG['BATTERY_SIZE'] += CONFIG['BATTERY_GROW']
            CONFIG['EQUIPMENT_COST'] += CONFIG['BATTERY_GROW_COST']

def main():

    parser = argparse.ArgumentParser(description='Solar and battery simulator')
    parser.add_argument('config', help='yml configuration file name')
    parser.add_argument('mode', help='Set the data mode which can be csv|api|profile')
    for item in CONFIG:
        parser.add_argument('--' + item, action='store', required=False, default=None)
    args = parser.parse_args()
    
    # Read config and override defaults
    with open(args.config, 'r') as fhan:
        yconfig = yaml.safe_load(fhan)
        for item in yconfig:
            if item not in CONFIG:
                print("ERROR: Bad configuration option in YML %s does not exist" % item)
                return(1)
            CONFIG[item] = yconfig[item]


    # Command line overrides
    for item in CONFIG:
        if item in args:
            value = getattr(args, item)
            if value:
                if isinstance(CONFIG[item], (int, float)):
                    CONFIG[item] = float(value)
                elif isinstance(CONFIG[item], bool):
                    CONFIG[item] = bool(value)
                else:
                    CONFIG[item] = value

    # Show configuration
    print(CONFIG)

    # Run a simulation
    simulate(args.mode)
    return 0

if __name__ == "__main__":
    exit(main())
