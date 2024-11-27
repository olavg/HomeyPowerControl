// Konfigurasjon
const heatingFutureHours = 24; // Vi ser nå 24 timer fremover for oppvarming
const chargingFutureHours = 12; // Timer fremover for lading
const tolerancePercentage = 10; // Hvor mye lavere nåværende pris må være for tiltak
const maxPowerUsageKW = 10; // Maksimalt strømforbruk i kW
const carTargetCharge = 70; // Ønsket ladestatus i prosent
const carChargeEndHour = 9; // Klokkeslett bilen må være ladet til
const dailyHeatingHours = 12; // Maksimalt antall timer varmtvannsberederen kan være på per dag
const maxContinuousOffHours = 4; // Maksimalt antall timer varmtvannsberederen kan være av sammenhengende
const now = new Date();
const currentHour = now.getHours();
const isDinnerTime = currentHour === 16;

// Enheter
const devices = await Homey.devices.getDevices();
const floorHeatingDevice = devices['FloorHeatingDeviceID']; // ID for gulvvarme
const waterHeaterDevice = Object.values(devices).find(device => device.name === 'WaterHeater'); // Varmtvannsbereder
const powerUsageDevice = Object.values(devices).find(device => device.name === 'PowerUsage');
const powerPriceDevice = Object.values(devices).find(device => device.name === 'PowerPrice');
const carStateDevice = devices['CarStateDeviceID']; // Enhet som rapporterer bilens ladestatus (prosent)

if (!powerPriceDevice || !powerUsageDevice) {
    console.error("Kritiske enheter som 'PowerPrice' eller 'PowerUsage' ble ikke funnet.");
    return;
}

if (!waterHeaterDevice) {
    console.error("Varmtvannsberederen 'WaterHeater' ble ikke funnet.");
    return;
}

// Hent eller opprett logikkvariabel for å holde styr på siste tidspunkt varmtvannsberederen var på
let lastHeaterOnTimestamp = await getLogicVariable('lastHeaterOnTimestamp');
if (lastHeaterOnTimestamp === null) {
    // Hvis variabelen ikke finnes, oppretter vi den og setter den til nåværende tidspunkt
    await setLogicVariable('lastHeaterOnTimestamp', now.getTime());
    lastHeaterOnTimestamp = now.getTime();
}

// Funksjon for å hente logikkvariabel
async function getLogicVariable(name) {
    const vars = await Homey.logic.getVariables();
    return vars[name] ? vars[name].value : null;
}

// Funksjon for å sette logikkvariabel
async function setLogicVariable(name, value) {
    const vars = await Homey.logic.getVariables();
    if (vars[name]) {
        await Homey.logic.updateVariable({ id: vars[name].id, variable: { value } });
    } else {
        await Homey.logic.createVariable({ name, type: 'number', value });
    }
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
    const currentPowerKW = powerUsageDevice.capabilitiesObj['measure_power']?.value / 1000 || 0; // Nåværende forbruk i kW
    const currentPrice = heatingPrices[0]; // Nåværende pris
    const validPrices = heatingPrices.filter(price => price !== null);
    const averageHeatingPrice = validPrices.reduce((sum, price) => sum + price, 0) / validPrices.length;

    console.log(`Nåværende strømpris: ${currentPrice} NOK/kWh`);
    console.log(`Gjennomsnittspris neste ${heatingFutureHours} timer: ${averageHeatingPrice.toFixed(4)} NOK/kWh`);
    console.log(`Nåværende strømforbruk: ${currentPowerKW.toFixed(2)} kW`);

    if (isDinnerTime) {
        console.log('Middagstid: Slår av gulvvarme og varmtvannsbereder.');
        // Slår AV varmtvannsberederen
        await controlWaterHeater(false);
        // Gulvvarme simuleres fortsatt
        console.log('- Gulvvarme ville vært slått AV.');
    } else if (currentPowerKW > maxPowerUsageKW) {
        console.log(`Høyt strømforbruk (${currentPowerKW.toFixed(2)} kW > ${maxPowerUsageKW} kW):`);
        // Slår AV varmtvannsberederen
        await controlWaterHeater(false);
        // Gulvvarme simuleres fortsatt
        console.log('- Gulvvarme ville vært slått AV.');
    } else {
        // Planlegger oppvarming av varmtvannsberederen basert på de billigste timene
        await scheduleWaterHeater();
        // Gulvvarme simuleres fortsatt
        console.log('- Gulvvarme ville blitt kontrollert basert på pris.');
    }
}

// Funksjon for å planlegge oppvarming av varmtvannsberederen
async function scheduleWaterHeater() {
    const heatingPrices = await getFuturePrices(24); // Henter priser for neste 24 timer
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
    const lastOnTime = new Date(parseInt(await getLogicVariable('lastHeaterOnTimestamp')));
    const hoursSinceLastOn = (now - lastOnTime) / (1000 * 60 * 60); // Konverterer fra ms til timer

    // Sjekker om nåværende time er blant de valgte oppvarmingstimene
    if (heatingHours.includes(currentHour) || hoursSinceLastOn >= maxContinuousOffHours) {
        // Sjekk om strømforbruket er under maksgrensen
        const currentPowerKW = powerUsageDevice.capabilitiesObj['measure_power']?.value / 1000 || 0;

        if (currentPowerKW < maxPowerUsageKW) {
            console.log('Slår PÅ varmtvannsberederen.');
            await controlWaterHeater(true);
            // Oppdaterer siste tidspunkt varmtvannsberederen var på
            await setLogicVariable('lastHeaterOnTimestamp', now.getTime());
        } else {
            console.log('Strømforbruket er for høyt. Slår AV varmtvannsberederen.');
            await controlWaterHeater(false);
        }
    } else {
        console.log('Slår AV varmtvannsberederen.');
        await controlWaterHeater(false);
    }
}

// Funksjon for å kontrollere varmtvannsberederen
async function controlWaterHeater(turnOn) {
    try {
        // Kontrollerer om enheten har kapabiliteten 'onoff'
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

// Funksjon for lading
async function manageCharging() {
    const chargingPrices = await getFuturePrices(chargingFutureHours);
    const validPrices = chargingPrices.filter(price => price !== null);

    const currentChargeLevel = carStateDevice?.capabilitiesObj['measure_battery']?.value || 0; // Bilens nåværende ladestatus
    const hoursToCharge = Math.ceil((carTargetCharge - currentChargeLevel) / 10); // Antall timer for å nå målet

    if (currentChargeLevel < carTargetCharge) {
        const sortedPrices = chargingPrices
            .slice(0, 24 - currentHour + carChargeEndHour) // Ser frem til kl 9 neste dag
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
