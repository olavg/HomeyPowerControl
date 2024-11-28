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
const currentMinute = now.getMinutes();
const isDinnerTime = currentHour === 16;
const isNightTime = currentHour >= 22 || currentHour < 5;
const highPriceDifferenceThreshold = 20; // 20%
const staggerWindowHighDifference = [0, 5, 10]; // Minutter etter timens start
const staggerWindowLowDifference = [50, 55, 0, 5, 10]; // Minutter før og etter timens start

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

// Tilordne spesifikke tidspunkter ut fra strømpris

let staggerMinutes;
if (priceDifferencePercent >= highPriceDifferenceThreshold) {
    staggerMinutes = staggerWindowHighDifference;
    console.log('Stor prisforskjell, bruker kortere tidsvindu for påslag.');
} else {
    staggerMinutes = staggerWindowLowDifference;
    console.log('Liten prisforskjell, bruker lengre tidsvindu for påslag.');
}

let deviceStaggerMinutes = global.get('deviceStaggerMinutes');
if (!deviceStaggerMinutes || Object.keys(deviceStaggerMinutes).length === 0) {
    deviceStaggerMinutes = {};
    floorHeatingDevices.forEach((device, index) => {
        const minuteIndex = index % staggerMinutes.length;
        deviceStaggerMinutes[device.id] = staggerMinutes[minuteIndex];
    });
    // Lagre til global variabel
    global.set('deviceStaggerMinutes', deviceStaggerMinutes);
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

    // Beregn prisforskjell i prosent
    const priceDifferencePercent = ((averageHeatingPrice - currentPrice) / averageHeatingPrice) * 100;
    console.log(`Prisforskjell: ${priceDifferencePercent.toFixed(2)}%`);

    // Bestem tidsvindu for påslag
    const highPriceDifferenceThreshold = 20; // 20%
    const staggerWindowHighDifference = [0, 5, 10]; // Minutter etter timens start
    const staggerWindowLowDifference = [50, 55, 0, 5, 10]; // Minutter før og etter timens start

    let staggerMinutes;
    if (priceDifferencePercent >= highPriceDifferenceThreshold) {
        staggerMinutes = staggerWindowHighDifference;
        console.log('Stor prisforskjell, bruker kortere tidsvindu for påslag.');
    } else {
        staggerMinutes = staggerWindowLowDifference;
        console.log('Liten prisforskjell, bruker lengre tidsvindu for påslag.');
    }

    // Tilordne enheter til minutter hvis ikke allerede gjort
    if (!deviceStaggerMinutes || Object.keys(deviceStaggerMinutes).length === 0) {
        deviceStaggerMinutes = {};
        floorHeatingDevices.forEach((device, index) => {
            const minuteIndex = index % staggerMinutes.length;
            deviceStaggerMinutes[device.id] = staggerMinutes[minuteIndex];
        });
        // Lagre til global variabel
        global.set('deviceStaggerMinutes', deviceStaggerMinutes);
    }

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

    // Beregn prisforskjell i prosent
    const currentPrice = heatingPrices[0];
    const averageHeatingPrice = validPrices.reduce((sum, item) => sum + item.price, 0) / validPrices.length;
    const priceDifferencePercent = ((averageHeatingPrice - currentPrice) / averageHeatingPrice) * 100;
    console.log(`Prisforskjell for varmtvannsbereder: ${priceDifferencePercent.toFixed(2)}%`);

    // Bestem tidsvindu for påslag
    const highPriceDifferenceThreshold = 20; // 20%
    const staggerWindowHighDifference = [0, 5, 10]; // Minutter etter timens start
    const staggerWindowLowDifference = [50, 55, 0, 5, 10]; // Minutter før og etter timens start

    let staggerMinutes;
    if (priceDifferencePercent >= highPriceDifferenceThreshold) {
        staggerMinutes = staggerWindowHighDifference;
        console.log('Stor prisforskjell, bruker kortere tidsvindu for påslag av varmtvannsbereder.');
    } else {
        staggerMinutes = staggerWindowLowDifference;
        console.log('Liten prisforskjell, bruker lengre tidsvindu for påslag av varmtvannsbereder.');
    }

    // Tildelt minutt for varmtvannsberederen
    let waterHeaterStaggerMinute = global.get('waterHeaterStaggerMinute');
    if (waterHeaterStaggerMinute === undefined || waterHeaterStaggerMinute === null) {
        // Tildel et tilfeldig minutt fra staggerMinutes
        const randomIndex = Math.floor(Math.random() * staggerMinutes.length);
        waterHeaterStaggerMinute = staggerMinutes[randomIndex];
        global.set('waterHeaterStaggerMinute', waterHeaterStaggerMinute);
    }

    const nowMinute = now.getMinutes();

    // Sjekk om det er tid for å kontrollere varmtvannsberederen
    if (nowMinute === waterHeaterStaggerMinute) {
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
    } else {
        console.log(`Ikke tid for å kontrollere varmtvannsberederen ennå. Tildelt minutt: ${waterHeaterStaggerMinute}`);
    }
}

// Funksjon for å planlegge oppvarming av gulvvarmen
async function scheduleFloorHeating() {
    const heatingPrices = await getFuturePrices(24);
    const currentPrice = heatingPrices[0];
    const validPrices = heatingPrices.filter(price => price !== null);
    const averageHeatingPrice = validPrices.reduce((sum, price) => sum + price, 0) / validPrices.length;

    // Beregn prisforskjell i prosent
    const priceDifferencePercent = ((averageHeatingPrice - currentPrice) / averageHeatingPrice) * 100;
    console.log(`Prisforskjell for gulvvarme: ${priceDifferencePercent.toFixed(2)}%`);

    // Bestem tidsvindu for påslag
    const highPriceDifferenceThreshold = 20; // 20%
    const staggerWindowHighDifference = [0, 5, 10]; // Minutter etter timens start
    const staggerWindowLowDifference = [50, 55, 0, 5, 10]; // Minutter før og etter timens start

    let staggerMinutes;
    if (priceDifferencePercent >= highPriceDifferenceThreshold) {
        staggerMinutes = staggerWindowHighDifference;
        console.log('Stor prisforskjell, bruker kortere tidsvindu for påslag av gulvvarme.');
    } else {
        staggerMinutes = staggerWindowLowDifference;
        console.log('Liten prisforskjell, bruker lengre tidsvindu for påslag av gulvvarme.');
    }

    // Tilordne enheter til minutter hvis ikke allerede gjort
    if (!deviceStaggerMinutes || Object.keys(deviceStaggerMinutes).length === 0) {
        deviceStaggerMinutes = {};
        floorHeatingDevices.forEach((device, index) => {
            const minuteIndex = index % staggerMinutes.length;
            deviceStaggerMinutes[device.id] = staggerMinutes[minuteIndex];
        });
        // Lagre til global variabel
        global.set('deviceStaggerMinutes', deviceStaggerMinutes);
    }

    const nowMinute = now.getMinutes();

    // Bestem maksimalt antall timer gulvvarmen kan være avhengig av tidspunktet på døgnet
    const maxContinuousOffHours = isNightTime ? maxContinuousOffHoursFloorHeatingNight : maxContinuousOffHoursFloorHeatingDay;

    for (const device of floorHeatingDevices) {
        const deviceId = device.id;
        const deviceStaggerMinute = deviceStaggerMinutes[deviceId];

        // Sjekk om det er tid for å kontrollere enheten
        if (nowMinute === deviceStaggerMinute) {
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
        } else {
            console.log(`Ikke tid for å kontrollere enheten '${device.name}' ennå. Tildelt minutt: ${deviceStaggerMinute}`);
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
    const hoursToCharge = Math.ceil((carTargetCharge - currentChargeLevel) / 10); // Antar 10% lading per time

    if (currentChargeLevel >= carTargetCharge) {
        console.log('Bilen er allerede ladet til ønsket nivå.');
        return;
    }

    // Beregn prisforskjell i prosent
    const currentPrice = chargingPrices[0];
    const averageChargingPrice = validPrices.reduce((sum, price) => sum + price, 0) / validPrices.length;
    const priceDifferencePercent = ((averageChargingPrice - currentPrice) / averageChargingPrice) * 100;
    console.log(`Prisforskjell for lading: ${priceDifferencePercent.toFixed(2)}%`);

    // Bestem tidsvindu for lading
    const highPriceDifferenceThreshold = 20; // 20%
    const staggerWindowHighDifference = [0, 5, 10]; // Minutter etter timens start
    const staggerWindowLowDifference = [50, 55, 0, 5, 10]; // Minutter før og etter timens start

    let staggerMinutes;
    if (priceDifferencePercent >= highPriceDifferenceThreshold) {
        staggerMinutes = staggerWindowHighDifference;
        console.log('Stor prisforskjell, bruker kortere tidsvindu for lading.');
    } else {
        staggerMinutes = staggerWindowLowDifference;
        console.log('Liten prisforskjell, bruker lengre tidsvindu for lading.');
    }

    // Tildelt minutt for lading
    let chargingStaggerMinute = global.get('chargingStaggerMinute');
    if (chargingStaggerMinute === undefined || chargingStaggerMinute === null) {
        // Tildel et tilfeldig minutt fra staggerMinutes
        const randomIndex = Math.floor(Math.random() * staggerMinutes.length);
        chargingStaggerMinute = staggerMinutes[randomIndex];
        global.set('chargingStaggerMinute', chargingStaggerMinute);
    }

    const nowMinute = now.getMinutes();

    // Sjekk om det er tid for å kontrollere ladingen
    if (nowMinute === chargingStaggerMinute) {
        // Bestem de beste ladetimene
        const sortedPrices = chargingPrices
            .slice(0, 24 - currentHour + carChargeEndHour)
            .map((price, index) => ({ price, hour: (currentHour + index) % 24 }))
            .sort((a, b) => a.price - b.price);

        const bestChargingHours = sortedPrices.slice(0, hoursToCharge).map(item => item.hour);

        console.log(`Beste timer for lading: ${bestChargingHours.map(hour => `Time ${hour}`).join(', ')}.`);

        // Sjekk om nåværende time er blant de beste ladetimene
        if (bestChargingHours.includes(currentHour)) {
            const currentPowerKW = powerUsageDevice.capabilitiesObj['measure_power']?.value / 1000 || 0;
            if (currentPowerKW < maxPowerUsageKW) {
                console.log('Starter lading av bilen.');
                await controlCharging(true); // Funksjon for å starte lading
            } else {
                console.log('Strømforbruket er for høyt. Stopper lading av bilen.');
                await controlCharging(false); // Funksjon for å stoppe lading
            }
        } else {
            console.log('Dette er ikke en av de beste ladetimene. Stopper lading av bilen.');
            await controlCharging(false); // Funksjon for å stoppe lading
        }
    } else {
        console.log(`Ikke tid for å kontrollere ladingen ennå. Tildelt minutt: ${chargingStaggerMinute}`);
    }
}

// Kjør begge funksjonene
await manageHeating();
await manageCharging();

return 'Skriptet er fullført.';
