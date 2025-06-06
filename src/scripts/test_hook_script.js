// --- Simple Frida Test Hook Script ---

console.log("[Test Script] Frida Test Script Loaded. Will attempt to attach and send messages.");

// Optional: Import frida-il2cpp-bridge if you want to test its availability,
// even if not using it extensively in this simple script.
// try {
//     Java.perform(function() { // Or Il2Cpp.perform if target is native
//         console.log("[Test Script] frida-il2cpp-bridge import attempted (if applicable).");
//     });
// } catch (e) {
//     console.warn("[Test Script] Could not perform Java.perform or Il2Cpp.perform for bridge import check: " + e);
// }

// --- Global Variables (Minimal) ---
let periodicSenderIntervalId = null;
let messageCounter = 0;

// --- Utility Function to Send Data to Python ---
function sendTestData(type, event, data = {}) {
    try {
        const message = {
            frida_type: 'test_data', // Differentiates from your 'game_data'
            type: type,
            timestamp: new Date().toISOString(),
            event_name: event,
            payload: data
        };
        send(message); // Frida's built-in send function
        // console.log("[Test Script] Sent: " + JSON.stringify(message));
    } catch (e) {
        console.error("[Test Script] Error sending test data: " + e);
    }
}

// --- Main Logic ---

// Send an immediate message once Frida is attached
sendTestData('status', 'script_attached', { message: 'Frida test script successfully attached and running.' });
console.log("[Test Script] 'script_attached' message sent.");

// Start sending periodic messages
if (periodicSenderIntervalId) {
    clearInterval(periodicSenderIntervalId);
}
periodicSenderIntervalId = setInterval(() => {
    messageCounter++;
    sendTestData('data', 'periodic_ping', {
        count: messageCounter,
        message: 'Periodic test ping from Frida script.',
        random_value: Math.random()
    });
    if (messageCounter % 10 === 0) { // Log to Frida console occasionally
        console.log(`[Test Script] Sent periodic_ping number ${messageCounter}`);
    }
}, 2000); // Send a message every 2 seconds

console.log("[Test Script] Periodic sender started (every 2 seconds).");

// Optional: Test basic Il2Cpp.perform if your target is an IL2CPP game
// This part is useful to see if the Il2Cpp environment can be accessed.
// If your target isn't IL2CPP, you can comment this out or use Java.perform for Android apps.
setTimeout(() => {
    try {
        console.log("[Test Script] Attempting Il2Cpp.perform() after a short delay...");
        Il2Cpp.perform(() => {
            console.log("[Test Script] Inside Il2Cpp.perform() callback.");
            const il2cppBase = Il2Cpp.module.base;
            sendTestData('status', 'il2cpp_perform_ok', {
                message: 'Il2Cpp.perform() executed successfully.',
                il2cpp_base: il2cppBase ? il2cppBase.toString() : "null"
            });
            console.log("[Test Script] Il2Cpp.module.base: " + (il2cppBase ? il2cppBase.toString() : "N/A"));
        });
    } catch (e) {
        console.error("[Test Script] Error during Il2Cpp.perform(): " + e);
        sendTestData('error', 'il2cpp_perform_failed', {
            message: 'Error during Il2Cpp.perform(): ' + e.message,
            stack: e.stack
        });
    }
}, 5000); // Delay Il2Cpp.perform to give the app time to load

// Example of how to receive messages from Python (if you implement that later)
// recv('python_command', function onMessage(message) {
//     console.log("[Test Script] Received message from Python: " + JSON.stringify(message));
//     if (message.payload && message.payload.action === 'stop_periodic') {
//         if (periodicSenderIntervalId) {
//             clearInterval(periodicSenderIntervalId);
//             periodicSenderIntervalId = null;
//             sendTestData('status', 'periodic_sender_stopped_by_python');
//             console.log("[Test Script] Periodic sender stopped by Python command.");
//         }
//     }
// });
// console.log("[Test Script] Ready to receive messages from Python on 'python_command' channel.");


console.log("[Test Script] Setup complete. Script will remain active.");
// --- END OF SCRIPT ---