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
const floorHeatingDevice = Object.values(devices).find(device => device.name === 'FloorHeating'); // Gulvvarme
const waterHeaterDevice = Object.values(devices).find(device => device.name === 'WaterHeater'); // Varmtvannsbereder
const powerUsageDevice = Object.values(devices).find(device => device.name === 'PowerUsage');
const powerPriceDevice = Object.values(devices).find(device => device.name === 'PowerPrice');
const carStateDevice = devices['CarStateDeviceID']; // Enhet som rapporterer bilens ladestatus (prosent)

// Sjekk kritiske enheter
if (!powerPriceDevice || !powerUsageDevice) {
    console.error("Kritiske enheter som 'PowerPrice' eller 'PowerUsage' ble ikke funnet.");
    return;
}

if (!waterHeaterDevice) {
    console.error("Varmtvannsberederen 'WaterHeater' ble ikke funnet.");
    return;
}

if (!floorHeatingDevice) {
    console.error("Gulvvarmeenheten 'FloorHeating' ble ikke funnet.");
    return;
}

// Hent eller opprett globale variabler for varmtvannsberederen
let lastHeaterOnTimestamp = global.get('lastHeaterOnTimestamp');
if (!lastHeaterOnTimestamp) {
    global.set('lastHeaterOnTimestamp', now.getTime());
    lastHeaterOnTimestamp = now.getTime();
}

// Hent eller opprett globale variabler for gulvvarmen
let lastFloorHeatingOnTimestamp = global.get('lastFloorHeatingOnTimestamp');
if (!lastFloorHeatingOnTimestamp) {
    global.set('lastFloorHeatingOnTimestamp', now.getTime());
    lastFloorHeatingOnTimestamp = now.getTime();
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
        await controlFloorHeating(false);
    } else if (currentPowerKW > maxPowerUsageKW) {
        console.log(`Høyt strømforbruk (${currentPowerKW.toFixed(2)} kW > ${maxPowerUsageKW} kW):`);
        await controlWaterHeater(false);
        await controlFloorHeating(false);
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

    // Beregn hvor lenge gulvvarmen har vært av
    const lastOnTime = new Date(parseInt(lastFloorHeatingOnTimestamp));
    const hoursSinceLastOn = (now - lastOnTime) / (1000 * 60 * 60);

    // Bestem maksimalt antall timer gulvvarmen kan være avhengig av tidspunktet på døgnet
    const maxContinuousOffHours = isNightTime ? maxContinuousOffHoursFloorHeatingNight : maxContinuousOffHoursFloorHeatingDay;

    // Sjekker om gulvvarmen har vært av for lenge
    if (hoursSinceLastOn >= maxContinuousOffHours) {
        const currentPowerKW = powerUsageDevice.capabilitiesObj['measure_power']?.value / 1000 || 0;
        if (currentPowerKW < maxPowerUsageKW) {
            console.log('Slår PÅ gulvvarmen.');
            await controlFloorHeating(true);
            global.set('lastFloorHeatingOnTimestamp', now.getTime());
        } else {
            console.log('Strømforbruket er for høyt. Holder gulvvarmen AV.');
            await controlFloorHeating(false);
        }
    } else {
        // Basert på pris, bestem om gulvvarmen skal være på
        const currentPrice = heatingPrices[0];
        const validPrices = heatingPrices.filter(price => price !== null);
        const averageHeatingPrice = validPrices.reduce((sum, price) => sum + price, 0) / validPrices.length;

        if (currentPrice < averageHeatingPrice) {
            console.log('Nåværende pris er under gjennomsnittet. Slår PÅ gulvvarmen.');
            await controlFloorHeating(true);
            global.set('lastFloorHeatingOnTimestamp', now.getTime());
        } else {
            console.log('Nåværende pris er over gjennomsnittet. Holder gulvvarmen AV.');
            await controlFloorHeating(false);
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

// Funksjon for å kontrollere gulvvarmen
async function controlFloorHeating(turnOn) {
    try {
        if (floorHeatingDevice.capabilities.includes('onoff')) {
            await floorHeatingDevice.setCapabilityValue('onoff', turnOn);
            console.log(`- Gulvvarmen er nå slått ${turnOn ? 'PÅ' : 'AV'}.`);
        } else {
            console.error("Gulvvarmen støtter ikke 'onoff'-kapabiliteten.");
        }
    } catch (error) {
        console.error('Feil ved kontroll av gulvvarmen:', error);
    }
}

// Funksjon for lading (uendret)
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
