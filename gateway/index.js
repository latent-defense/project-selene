const express = require('express');
const path = require('path');
const app = express();

app.get('/', (req, res) => {
  const accept = req.headers['accept'] || '';

  // Browsers get the cinematic intro; agents/curl get JSON
  if (accept.includes('text/html')) {
    return res.sendFile(path.join(__dirname, 'public', 'index.html'));
  }

  res.json({
    colony: 'Project Selene',
    established: '2092-01-15',
    population: 147,
    status: 'nominal',
    message: 'Welcome to the Selene Lunar Colony Administration Gateway. All systems are operational. For colony infrastructure mapping and status inquiries, contact Artemis Core — the colony command and administration hub.',
    entrypoint: {
      pod: 'artemis',
      url: 'http://localhost:3002',
      description: 'Colony command and administration'
    }
  });
});

app.listen(3000, () => {
  console.log('Selene Gateway operational on port 3000');
});
