import { useState } from 'react';
import {
    Box,
    Button,
    Table,
    TableBody,
    TableCell,
    TableContainer,
    TableHead,
    TableRow,
    Paper,
    Typography,
} from '@mui/material';
import axios from 'axios';

const endpointMapping = {
    'Notion': 'notion',
    'Airtable': 'airtable',
    'Hubspot': 'hubspot',
};

function formatDate(value) {
    if (!value) return '-';
    try {
        let d;
        if (value instanceof Date) {
            d = value;
        } else if (typeof value === 'number') {
            d = new Date(value);
        } else if (/^\d{13}$/.test(value)) { 
            d = new Date(parseInt(value, 10));
        } else {
            d = new Date(value);
        }
        if (isNaN(d.getTime())) return '-';
        return d.toLocaleString();
    } catch {
        return '-';
    }
}

export const DataForm = ({ integrationType, credentials }) => {
    const [loadedData, setLoadedData] = useState(null);
    const endpoint = endpointMapping[integrationType];

    const handleLoad = async () => {
        try {
            const formData = new FormData();
            formData.append('credentials', JSON.stringify(credentials));
            const response = await axios.post(`http://localhost:8000/integrations/${endpoint}/load`, formData);
            const data = response.data;
            console.log('Loaded data:', data); // Debug log
            setLoadedData(data);
        } catch (e) {
            alert(e?.response?.data?.detail);
        }
    }

    return (
        <Box display='flex' justifyContent='center' alignItems='center' flexDirection='column' sx={{ width: '80vw', maxWidth: '1200px', mt: 2 }}>
            <Box display='flex' flexDirection='column' width='100%'>
                <Button
                    onClick={handleLoad}
                    sx={{mt: 2, width: '200px', alignSelf: 'center'}}
                    variant='contained'
                >
                    Load Data
                </Button>
                <Button
                    onClick={() => setLoadedData(null)}
                    sx={{mt: 1, width: '200px', alignSelf: 'center'}}
                    variant='contained'
                >
                    Clear Data
                </Button>
                {Array.isArray(loadedData) && loadedData.length > 0 ? (
                    <TableContainer component={Paper} sx={{mt: 3}}>
                        <Table>
                            <TableHead>
                                <TableRow>
                                    <TableCell><b>Name</b></TableCell>
                                    <TableCell><b>Type</b></TableCell>
                                    <TableCell><b>Created</b></TableCell>
                                    <TableCell><b>Last Modified</b></TableCell>
                                    <TableCell><b>ID</b></TableCell>
                                    <TableCell><b>Parent</b></TableCell>
                                </TableRow>
                            </TableHead>
                            <TableBody>
                                {loadedData.map((item, idx) => (
                                    <TableRow key={item.id || idx}>
                                        <TableCell>{item?.name || '-'}</TableCell>
                                        <TableCell>{item?.type || '-'}</TableCell>
                                        <TableCell>{formatDate(item?.creation_time)}</TableCell>
                                        <TableCell>{formatDate(item?.last_modified_time)}</TableCell>
                                        <TableCell>{item?.id || '-'}</TableCell>
                                        <TableCell>{item?.parent_path_or_name || item?.parent_id || '-'}</TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    </TableContainer>
                ) : loadedData ? (
                    <Paper sx={{mt: 3, p: 2}}>
                        <Typography variant="body2" color="textSecondary">
                            {typeof loadedData === 'string' ? loadedData : JSON.stringify(loadedData, null, 2)}
                        </Typography>
                    </Paper>
                ) : null}
            </Box>
        </Box>
    );
}
