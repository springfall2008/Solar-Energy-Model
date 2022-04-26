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

# Default configuration - overriden by YML
CONFIG = {
    'NIGHT_START' : 1,
    'NIGHT_END' : 5,
    'BATTERY_SIZE' : 0,
    'BATTERY_LOSS' : 0.96,
    'BATTERY_DOD'  : 0.90,
    'BATTERY_PEAK_DRAW' : 3.0,
    'BATTERY_MAX_CHARGE_RATE' : 3.0,
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
    'CONSUMPTION' : "consumption.csv",
    'SUNRISE': "sunrise.txt"
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
        
    def __init__(self, filename):
        self.data = {}
        self.total_used = 0
        
        with open(filename, 'r') as han:
            last_hour = -1
            for line in han:
                if line:
                    line = line.strip()
                    fields = line.split(',')
                    if not fields[0].startswith('Consumption'):
                        energy = float(fields[0])
                        start_date, start_time = fields[1].split('T')
                        start_time, offset_time = start_time.split('+')
                        start = datetime.strptime(start_date.strip() + " " + start_time, '%Y-%m-%d %H:%M:%S')
                        day_of_year = start.timetuple().tm_yday
                        hour_of_day = start.hour
                        if day_of_year not in self.data:
                            self.data[day_of_year] = {}
                        if hour_of_day not in self.data[day_of_year]:
                            self.data[day_of_year][hour_of_day] = energy
                        elif last_hour == hour_of_day:
                            self.data[day_of_year][hour_of_day] += energy
                        else:
                            # If the data covers multiple years use the latest only
                            self.data[day_of_year][hour_of_day] = energy
                        last_hour = hour_of_day
                            
    def get_load(self, day, hour):
        if day in self.data:
            if hour in self.data[day]:
                if self.data[day][hour]:
                    return self.data[day][hour]
        return 0

    def show(self):
        print ("Load used %lf kw" % self.total_used)

def run_scenario(show=True):
    if show:
        print ("---------- BATTERY %f SOLAR %f --------" % (CONFIG['BATTERY_SIZE'], CONFIG['SOLAR_SIZE']))

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

    # Octopus data
    load = cl_load(CONFIG['CONSUMPTION'])

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
                if hour >= CONFIG['NIGHT_START'] and hour <= CONFIG['NIGHT_END']:
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


def main():

    parser = argparse.ArgumentParser(description='Solar and battery simulator')
    parser.add_argument('config', help='yml configuration file name')
    args = parser.parse_args()
    
    # Read config and override defaults
    with open(args.config, 'r') as fhan:
        yconfig = yaml.safe_load(fhan)
        for item in yconfig:
            CONFIG[item] = yconfig[item]
    print(CONFIG)

    total_cost = 0
    base_cost = 0
    year = 0
    while year < CONFIG['YEARS']:
        tempb = CONFIG['BATTERY_SIZE']
        temps = CONFIG['SOLAR_SIZE']
        CONFIG['BATTERY_SIZE'] = 0
        CONFIG['SOLAR_SIZE'] = 0

        base_cost += run_scenario(False)

        CONFIG['BATTERY_SIZE'] = tempb
        CONFIG['SOLAR_SIZE'] = temps
        total_cost += run_scenario(year==0)
        year += 1

        print("Year %d - Total cost: %0.2f base cost %0.2f saving %0.2f" % (year, total_cost, base_cost, base_cost - total_cost - CONFIG['EQUIPMENT_COST']))

        # Annual adjustments
        CONFIG['BATTERY_SIZE'] *= CONFIG['ANNUAL_BATTERY_LOSS'] # Loss of battery capacity
        CONFIG['PRICE_DAY']    *= CONFIG['INFLATION'] # Inflation for electric costs
        CONFIG['PRICE_NIGHT']  *= CONFIG['INFLATION'] # Inflation for electric costs

if __name__ == "__main__":
    main()
