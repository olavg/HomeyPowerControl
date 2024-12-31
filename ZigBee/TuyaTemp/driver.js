'use strict';

const { ZigBeeDevice } = require('homey-zigbee-driver');

class TemperatureSensor extends ZigBeeDevice {
  async onNodeInit({ zclNode }) {
    this.log(`Temperature Sensor initialized`);

    // Registrer målinger for temperatur og fuktighet
    this.registerCapability('measure_temperature', 'msTemperatureMeasurement');
    this.registerCapability('measure_humidity', 'msRelativeHumidity');
  }
}

module.exports = TemperatureSensor;
