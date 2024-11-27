// Konfigurasjon
const heatingFutureHours = 24; // Vi ser 24 timer fremover for oppvarming
const chargingFutureHours = 12; // Timer fremover for lading
const maxPowerUsageKW = 10; // Maksimalt strømforbruk i kW
const carTargetCharge = 70; // Ønsket ladestatus i prosent
const carChargeEndHour = 9; // Klokkeslett bilen må være ladet til
const dailyHeatingHours = 12; // Antall timer varmtvannsberederen skal være på per dag
const maxContinuousOffHoursWaterHeater = 4; // Maksimalt antall timer varmtvannsberederen kan være av sammenhengende
const maxContinuousOffHoursFloorHeatingDay = 1; // Maksimalt av-tid for gulvvarme på dagtid
const maxContinuousOffHoursFloorHeatingNight = 2; // Maksimalt av-tid for gulvvarme om natten
const now = new Date();
const currentHour = now.getHours();
const isDinnerTime = currentHour === 16;
const isNightTime = currentHour >= 22 || currentHour < 5;

// Enheter
const devices = await Homey.devices.getDevices();
const waterHeaterDevice = Object.values(devices).find(device => device.name === 'WaterHeater'); // Varmtvannsbereder
const powerUsageDevice = Object.values(devices).find(device => device.name === 'PowerUsage');
const powerPriceDevice = Object.values(devices).find(device => device.name === 'PowerPrice');
const carStateDevice = devices['CarStateDeviceID']; // Enhet som rapporterer bilens ladestatus (prosent)

// Samle alle gulvvarmeenhetene (antar at enhetsnavnene slutter med 'gulvvarme')
const floorHeatingDevices = Object.values(devices).filter(device => device.name.endsWith('gulvvarme'));

if (!powerPriceDevice || !powerUsageDevice) {
    console.error("Kritiske enheter som 'PowerPrice' eller 'PowerUsage' ble ikke funnet.");
    return;
}

if (!waterHeaterDevice) {
    console.error("Varmtvannsberederen 'WaterHeater' ble ikke funnet.");
    return;
}

if (floorHeatingDevices.length === 0) {
    console.error("Ingen gulvvarmeenheter ble funnet.");
    return;
}

// Hent eller opprett globale variabler for varmtvannsberederen
let lastHeaterOnTimestamp = global.get('lastHeaterOnTimestamp');
if (!lastHeaterOnTimestamp) {
    global.set('lastHeaterOnTimestamp', now.getTime());
    lastHeaterOnTimestamp = now.getTime();
}

// Hent eller opprett globale variabler for gulvvarmen
let lastFloorHeatingOnTimestamps = {};
const lastFloorHeatingOnTimestampsString = global.get('lastFloorHeatingOnTimestamps');
if (lastFloorHeatingOnTimestampsString) {
    try {
        lastFloorHeatingOnTimestamps = JSON.parse(lastFloorHeatingOnTimestampsString);
    } catch (e) {
        console.error('Kunne ikke parse lastFloorHeatingOnTimestamps:', e);
        lastFloorHeatingOnTimestamps = {};
    }
} else {
    global.set('lastFloorHeatingOnTimestamps', JSON.stringify(lastFloorHeatingOnTimestamps));
}

// Hent eller opprett globale variabler for opprinnelige måltemperaturer
let originalTargetTemperatures = {};
const originalTempsString = global.get('originalTargetTemperatures');
if (originalTempsString) {
    try {
        originalTargetTemperatures = JSON.parse(originalTempsString);
    } catch (e) {
        console.error('Kunne ikke parse originalTargetTemperatures:', e);
        originalTargetTemperatures = {};
    }
} else {
    global.set('originalTargetTemperatures', JSON.stringify(originalTargetTemperatures));
}

// Funksjon for å hente fremtidige priser
async function getFuturePrices(hours) {
    const prices = [];
    let lastValidPrice = null;
    for (let i = 0; i < hours; i++) {
        const capabilityName = `measure_power.h${i}`;
        const price = powerPriceDevice.capabilitiesObj[capabilityName]?.value;
        if (price !== undefined && !isNaN(price)) {
            lastValidPrice = parseFloat(price.toFixed(4));
            prices.push(lastValidPrice);
        } else if (lastValidPrice !== null) {
            prices.push(lastValidPrice); // Gjenta siste gyldige pris
        } else {
            console.error(`Ingen gyldig pris funnet for time ${i}`);
            prices.push(null);
        }
    }
    return prices;
}

// Funksjon for oppvarming
async function manageHeating() {
    const heatingPrices = await getFuturePrices(heatingFutureHours);
    const currentPowerKW = powerUsageDevice.capabilitiesObj['measure_power']?.value / 1000 || 0;
    const currentPrice = heatingPrices[0];
    const validPrices = heatingPrices.filter(price => price !== null);
    const averageHeatingPrice = validPrices.reduce((sum, price) => sum + price, 0) / validPrices.length;

    console.log(`Nåværende strømpris: ${currentPrice} NOK/kWh`);
    console.log(`Gjennomsnittspris neste ${heatingFutureHours} timer: ${averageHeatingPrice.toFixed(4)} NOK/kWh`);
    console.log(`Nåværende strømforbruk: ${currentPowerKW.toFixed(2)} kW`);

    if (isDinnerTime) {
        console.log('Middagstid: Slår av gulvvarme og varmtvannsbereder.');
        await controlWaterHeater(false);
        await controlAllFloorHeatings(false);
    } else if (currentPowerKW > maxPowerUsageKW) {
        console.log(`Høyt strømforbruk (${currentPowerKW.toFixed(2)} kW > ${maxPowerUsageKW} kW):`);
        await controlWaterHeater(false);
        await controlAllFloorHeatings(false);
    } else {
        await scheduleWaterHeater();
        await scheduleFloorHeating();
    }
}

// Funksjon for å planlegge oppvarming av varmtvannsberederen
async function scheduleWaterHeater() {
    const heatingPrices = await getFuturePrices(24);
    const validPrices = heatingPrices.map((price, index) => ({
        price,
        hour: (currentHour + index) % 24
    })).filter(item => item.price !== null);

    // Filtrer bort uønskede timer (f.eks. middagstid)
    const filteredPrices = validPrices.filter(item => item.hour !== 16);

    // Sorterer prisene fra lavest til høyest
    filteredPrices.sort((a, b) => a.price - b.price);

    // Velger de billigste timene for oppvarming
    const heatingHours = filteredPrices.slice(0, Math.ceil(dailyHeatingHours)).map(item => item.hour);

    console.log(`Planlagte oppvarmingstimer for varmtvannsbereder: ${heatingHours.join(', ')}.`);

    // Beregn hvor lenge varmtvannsberederen har vært av
    const lastOnTime = new Date(parseInt(lastHeaterOnTimestamp));
    const hoursSinceLastOn = (now - lastOnTime) / (1000 * 60 * 60);

    // Sjekker om nåværende time er blant de valgte oppvarmingstimene eller om den har vært av for lenge
    if (heatingHours.includes(currentHour) || hoursSinceLastOn >= maxContinuousOffHoursWaterHeater) {
        const currentPowerKW = powerUsageDevice.capabilitiesObj['measure_power']?.value / 1000 || 0;
        if (currentPowerKW < maxPowerUsageKW) {
            console.log('Slår PÅ varmtvannsberederen.');
            await controlWaterHeater(true);
            global.set('lastHeaterOnTimestamp', now.getTime());
        } else {
            console.log('Strømforbruket er for høyt. Slår AV varmtvannsberederen.');
            await controlWaterHeater(false);
        }
    } else {
        console.log('Slår AV varmtvannsberederen.');
        await controlWaterHeater(false);
    }
}

// Funksjon for å planlegge oppvarming av gulvvarmen
async function scheduleFloorHeating() {
    const heatingPrices = await getFuturePrices(24);
    const currentPrice = heatingPrices[0];
    const validPrices = heatingPrices.filter(price => price !== null);
    const averageHeatingPrice = validPrices.reduce((sum, price) => sum + price, 0) / validPrices.length;

    // Bestem maksimalt antall timer gulvvarmen kan være avhengig av tidspunktet på døgnet
    const maxContinuousOffHours = isNightTime ? maxContinuousOffHoursFloorHeatingNight : maxContinuousOffHoursFloorHeatingDay;

    for (const device of floorHeatingDevices) {
        const deviceId = device.id;

        // Hent eller opprett siste på-tidspunkt for denne enheten
        let lastOnTimestamp = lastFloorHeatingOnTimestamps[deviceId];
        if (!lastOnTimestamp) {
            lastOnTimestamp = now.getTime();
            lastFloorHeatingOnTimestamps[deviceId] = now.getTime();
            global.set('lastFloorHeatingOnTimestamps', JSON.stringify(lastFloorHeatingOnTimestamps));
        }

        const lastOnTime = new Date(parseInt(lastOnTimestamp));
        const hoursSinceLastOn = (now - lastOnTime) / (1000 * 60 * 60);

        // Sjekk om enheten har vært av for lenge
        if (hoursSinceLastOn >= maxContinuousOffHours) {
            const currentPowerKW = powerUsageDevice.capabilitiesObj['measure_power']?.value / 1000 || 0;
            if (currentPowerKW < maxPowerUsageKW) {
                console.log(`Slår PÅ gulvvarmeenheten '${device.name}' (har vært av i ${hoursSinceLastOn.toFixed(2)} timer).`);
                await controlFloorHeating(device, true);
                lastFloorHeatingOnTimestamps[deviceId] = now.getTime();
                global.set('lastFloorHeatingOnTimestamps', JSON.stringify(lastFloorHeatingOnTimestamps));
            } else {
                console.log(`Strømforbruket er for høyt. Holder gulvvarmeenheten '${device.name}' AV.`);
                await controlFloorHeating(device, false);
            }
        } else {
            // Basert på pris, bestem om gulvvarmeenheten skal være på
            if (currentPrice < averageHeatingPrice) {
                console.log(`Nåværende pris er under gjennomsnittet. Slår PÅ gulvvarmeenheten '${device.name}'.`);
                await controlFloorHeating(device, true);
                lastFloorHeatingOnTimestamps[deviceId] = now.getTime();
                global.set('lastFloorHeatingOnTimestamps', JSON.stringify(lastFloorHeatingOnTimestamps));
            } else {
                console.log(`Nåværende pris er over gjennomsnittet. Holder gulvvarmeenheten '${device.name}' AV.`);
                await controlFloorHeating(device, false);
            }
        }
    }
}

// Funksjon for å kontrollere varmtvannsberederen
async function controlWaterHeater(turnOn) {
    try {
        if (waterHeaterDevice.capabilities.includes('onoff')) {
            await waterHeaterDevice.setCapabilityValue('onoff', turnOn);
            console.log(`- Varmtvannsbereder er nå slått ${turnOn ? 'PÅ' : 'AV'}.`);
        } else {
            console.error("Varmtvannsberederen støtter ikke 'onoff'-kapabiliteten.");
        }
    } catch (error) {
        console.error('Feil ved kontroll av varmtvannsberederen:', error);
    }
}

// Funksjon for å kontrollere en enkelt gulvvarmeenhet ved å justere måltemperaturen
async function controlFloorHeating(device, turnOn) {
    try {
        if (device.capabilities.includes('target_temperature')) {
            const deviceId = device.id;
            // Lagre opprinnelig måltemperatur hvis den ikke allerede er lagret
            if (originalTargetTemperatures[deviceId] === undefined) {
                const currentTemp = device.capabilitiesObj['target_temperature'].value;
                console.log(`- Lagrer opprinnelig temperatur for '${device.name}': ${currentTemp}°C.`);
                originalTargetTemperatures[deviceId] = currentTemp;
                global.set('originalTargetTemperatures', JSON.stringify(originalTargetTemperatures));
            }
            const originalTemp = originalTargetTemperatures[deviceId];
            if (turnOn) {
                console.log(`- Gjenoppretter opprinnelig temperatur for '${device.name}': ${originalTemp}°C.`);
                await device.setCapabilityValue('target_temperature', originalTemp);
                console.log(`- Gulvvarmeenheten '${device.name}' er nå satt til opprinnelig temperatur: ${originalTemp}°C.`);
            } else {
                // Sett måltemperaturen til 5°C under opprinnelig temperatur
                const setbackTemp = Math.max(originalTemp - 5, 5); // Sikrer at temperaturen ikke settes under 5°C
                console.log(`- Reduserer temperaturen for '${device.name}' til ${setbackTemp}°C (5°C under opprinnelig).`);
                await device.setCapabilityValue('target_temperature', setbackTemp);
                console.log(`- Gulvvarmeenheten '${device.name}' er nå satt til redusert temperatur: ${setbackTemp}°C.`);
            }
        } else {
            console.error(`Gulvvarmeenheten '${device.name}' støtter ikke 'target_temperature'-kapabiliteten.`);
        }
    } catch (error) {
        console.error(`Feil ved kontroll av gulvvarmeenheten '${device.name}':`, error);
    }
}

// Funksjon for å slå av eller på alle gulvvarmeenheter
async function controlAllFloorHeatings(turnOn) {
    for (const device of floorHeatingDevices) {
        await controlFloorHeating(device, turnOn);
        if (turnOn) {
            lastFloorHeatingOnTimestamps[device.id] = now.getTime();
            global.set('lastFloorHeatingOnTimestamps', JSON.stringify(lastFloorHeatingOnTimestamps));
        }
    }
}

// Funksjon for lading
async function manageCharging() {
    const chargingPrices = await getFuturePrices(chargingFutureHours);
    const validPrices = chargingPrices.filter(price => price !== null);

    const currentChargeLevel = carStateDevice?.capabilitiesObj['measure_battery']?.value || 0;
    const hoursToCharge = Math.ceil((carTargetCharge - currentChargeLevel) / 10);

    if (currentChargeLevel < carTargetCharge) {
        const sortedPrices = chargingPrices
            .slice(0, 24 - currentHour + carChargeEndHour)
            .map((price, index) => ({ price, index }))
            .sort((a, b) => a.price - b.price);

        const bestChargingHours = sortedPrices.slice(0, hoursToCharge).map(hour => hour.index);
        console.log(`Beste timer for lading: ${bestChargingHours.map(hour => `Time ${hour}`).join(', ')}`);
    } else {
        console.log('Bilen er allerede ladet til ønsket nivå.');
    }
}

// Kjør begge funksjonene
await manageHeating();
await manageCharging();

return 'Skriptet er fullført.';
