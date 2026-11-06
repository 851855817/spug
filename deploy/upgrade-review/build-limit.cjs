// Old webpack minifiers size their process pool from os.cpus(), not free memory.
// Build serially on the developer machine; this does not change application code.
const os = require('os');
const cpus = os.cpus;
os.cpus = () => cpus().slice(0, 1);
if (os.availableParallelism) os.availableParallelism = () => 1;
