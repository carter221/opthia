import React, { useState, useEffect } from 'react';
import { 
  Activity, 
  UploadCloud, 
  Users, 
  Database, 
  MessageSquare, 
  CheckCircle, 
  AlertCircle, 
  TrendingUp, 
  ChevronRight, 
  Layers, 
  Download,
  Send,
  Loader2,
  Lock,
  RefreshCw,
  FileSpreadsheet,
  User,
  LogOut,
  KeyRound,
  ShieldCheck,
  Camera
} from 'lucide-react';

export default function App() {
  const [activeTab, setActiveTab] = useState('dashboard');
  const [patients, setPatients] = useState([]);
  const [selectedPatient, setSelectedPatient] = useState(null);
  const [uploadQueue, setUploadQueue] = useState([]);
  const [batchName, setBatchName] = useState('');
  const [isUploading, setIsUploading] = useState(false);
  const [batchId, setBatchId] = useState(null);
  const [batchProgress, setBatchProgress] = useState(null);
  const [isPolling, setIsPolling] = useState(false);
  
  // Chatbot State
  const [chatMessage, setChatMessage] = useState('');
  const [chatHistory, setChatHistory] = useState([]);
  const [isChatLoading, setIsChatLoading] = useState(false);

  // Admin Verification State
  const [adminComments, setAdminComments] = useState('');

  // Authentication State
  const [token, setToken] = useState(localStorage.getItem('opthia_token') || null);
  const [user, setUser] = useState(JSON.parse(localStorage.getItem('opthia_user')) || null);
  const [authMode, setAuthMode] = useState('login'); // login ou register
  const [authForm, setAuthForm] = useState({ name: '', email: '', password: '', role: 'doctor' });
  const [authError, setAuthError] = useState('');
  const [profileForm, setProfileForm] = useState({ name: '', password: '' });
  const [profileMessage, setProfileMessage] = useState('');
  
  // PhoneCapture States
  const [cameraStream, setCameraStream] = useState(null);
  const [capturedPhoto, setCapturedPhoto] = useState(null);
  const [dicomPatient, setDicomPatient] = useState({ name: '', id: '', birthDate: '' });
  const [isSendingDicom, setIsSendingDicom] = useState(false);

  // Toggle States for Modal Image preview
  const [showGradCam, setShowGradCam] = useState(true);
  const [viewMode, setViewMode] = useState('raw'); // 'raw', 'cropped', 'gradcam'
  const [isFullScreenImage, setIsFullScreenImage] = useState(false);

  // Stats
  const [stats, setStats] = useState({
    total: 0,
    positive: 0,
    negative: 0,
    validated: 0
  });

  const backendUrl = import.meta.env.VITE_BACKEND_URL || 'https://api-ophtia.sjudicael.top';

  const fetchPatients = async () => {
    try {
      const res = await fetch(`${backendUrl}/api/diagnostics`, {
        headers: {
          'Authorization': `Bearer ${token}`
        }
      });
      if (res.ok) {
        const data = await res.json();
        setPatients(data);
        updateStats(data);
      }
    } catch (e) {
      console.error("Erreur de recuperation des patients:", e);
    }
  };

  useEffect(() => {
    if (token) {
      fetchPatients();
    } else {
      setPatients([]);
      setStats({ total: 0, positive: 0, negative: 0, validated: 0 });
    }
    
    if (user) {
      setProfileForm({ name: user.name, password: '' });
    }
  }, [user, token]);

  const updateStats = (list) => {
    const total = list.length;
    const positive = list.filter(p => p.result?.prediction_class === 1).length;
    const negative = total - positive;
    const validated = list.filter(p => p.validated_by_admin).length;
    setStats({ total, positive, negative, validated });
  };

  // Poll batch status
  useEffect(() => {
    let timer;
    if (isPolling && batchId) {
      const poll = async () => {
        try {
          const res = await fetch(`${backendUrl}/api/batch/status/${batchId}`);
          if (res.ok) {
            const data = await res.json();
            setBatchProgress(data);
            if (data.status === 'completed') {
              setIsPolling(false);
              // Fetch final batch results
              const resultsRes = await fetch(`${backendUrl}/api/batch/results/${batchId}`);
              if (resultsRes.ok) {
                const batchResults = await resultsRes.json();
                setPatients(prev => {
                  const updated = [...batchResults, ...prev.filter(p => p.batch_id !== batchId)];
                  updateStats(updated);
                  return updated;
                });
              }
            }
          }
        } catch (e) {
          console.error(e);
        }
      };
      timer = setInterval(poll, 1500);
    }
    return () => clearInterval(timer);
  }, [isPolling, batchId]);

  // Auth Submit
  const handleAuthSubmit = async (e) => {
    e.preventDefault();
    setAuthError('');
    const endpoint = authMode === 'login' ? '/api/auth/login' : '/api/auth/register';
    
    try {
      const res = await fetch(`${backendUrl}${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(authForm)
      });
      const data = await res.json();

      if (res.ok) {
        if (authMode === 'login') {
          localStorage.setItem('opthia_token', data.token);
          localStorage.setItem('opthia_user', JSON.stringify(data.user));
          setToken(data.token);
          setUser(data.user);
          setActiveTab('dashboard');
        } else {
          alert('Compte créé avec succès, vous pouvez maintenant vous connecter.');
          setAuthMode('login');
        }
      } else {
        setAuthError(data.error || 'Une erreur est survenue.');
      }
    } catch (err) {
      setAuthError('Impossible de contacter le serveur.');
    }
  };

  // Update Profile Submit
  const handleProfileUpdate = async (e) => {
    e.preventDefault();
    setProfileMessage('');
    
    try {
      const res = await fetch(`${backendUrl}/api/auth/update`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({
          name: profileForm.name,
          ...(profileForm.password ? { password: profileForm.password } : {})
        })
      });

      if (res.ok) {
        const updatedUser = { ...user, name: profileForm.name };
        localStorage.setItem('opthia_user', JSON.stringify(updatedUser));
        setUser(updatedUser);
        setProfileMessage('Profil mis à jour avec succès !');
        setProfileForm(prev => ({ ...prev, password: '' }));
      } else {
        const data = await res.json();
        setProfileMessage(`Erreur: ${data.error}`);
      }
    } catch (err) {
      setProfileMessage('Erreur de connexion au serveur.');
    }
  };

  const logout = () => {
    localStorage.removeItem('opthia_token');
    localStorage.removeItem('opthia_user');
    setToken(null);
    setUser(null);
    setActiveTab('dashboard');
  };

  // Handle batch file upload simulation
  const handleFolderUpload = (e) => {
    const files = Array.from(e.target.files);
    if (!files.length) return;

    const queue = files.map((file, index) => {
      const cleanedName = file.name.split('.')[0].replace(/[-_]/g, ' ');
      const capitalize = cleanedName.charAt(0).toUpperCase() + cleanedName.slice(1);
      
      return {
        id: index,
        patient_name: capitalize || `Patient #${index + 1}`,
        filename: file.name,
        file: file,
        model_type: index % 2 === 0 ? 'rd' : 'glaucoma'
      };
    });
    
    setUploadQueue(queue);
    setBatchName(`Lot de diagnostic - ${files.length} patients`);
  };

  const submitBatchUpload = async () => {
    if (!uploadQueue.length) return;
    setIsUploading(true);

    try {
      const preparedPatients = await Promise.all(
        uploadQueue.map(async (item) => {
          return new Promise((resolve) => {
            const reader = new FileReader();
            reader.readAsDataURL(item.file);
            reader.onload = () => {
              resolve({
                patient_name: item.patient_name,
                filename: item.filename,
                model_type: item.model_type,
                image_base64: reader.result
              });
            };
          });
        })
      );

      const res = await fetch(`${backendUrl}/api/batch/submit`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({
          batch_name: batchName,
          patients: preparedPatients
        })
      });

      if (res.ok) {
        const data = await res.json();
        setBatchId(data.batch_id);
        setIsPolling(true);
        setUploadQueue([]);
        setActiveTab('dashboard');
      } else {
        alert("Erreur lors de la soumission du lot.");
      }
    } catch (e) {
      console.error(e);
      alert("Erreur de connexion au serveur.");
    } finally {
      setIsUploading(false);
    }
  };

  // Submit chat message
  const handleSendMessage = async (e) => {
    e.preventDefault();
    if (!chatMessage.trim()) return;

    const userMsg = { role: 'user', text: chatMessage };
    setChatHistory(prev => [...prev, userMsg]);
    setChatMessage('');
    setIsChatLoading(true);

    try {
      const res = await fetch(`${backendUrl}/api/chat`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({
          task_id: selectedPatient?.task_id,
          message: chatMessage
        })
      });

      if (res.ok) {
        const data = await res.json();
        setChatHistory(prev => [...prev, { role: 'assistant', text: data.response }]);
      } else {
        setChatHistory(prev => [...prev, { role: 'assistant', text: "Désolé, une erreur s'est produite lors de l'appel de l'assistant." }]);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setIsChatLoading(false);
    }
  };

  // Confirm/Correct Label
  const confirmLabel = async (confirmedClass) => {
    if (!selectedPatient) return;
    try {
      const res = await fetch(`${backendUrl}/api/diagnostic/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_id: selectedPatient.task_id,
          confirmed_class: confirmedClass,
          comments: adminComments
        })
      });

      if (res.ok) {
        setPatients(prev => {
          const updated = prev.map(p => {
            if (p.task_id === selectedPatient.task_id) {
              return {
                ...p,
                validated_by_admin: true,
                confirmed_class: confirmedClass,
                admin_comments: adminComments
              };
            }
            return p;
          });
          updateStats(updated);
          return updated;
        });
        setSelectedPatient(prev => ({
          ...prev,
          validated_by_admin: true,
          confirmed_class: confirmedClass,
          admin_comments: adminComments
        }));
        alert("Diagnostic validé et labellisé avec succès !");
      }
    } catch (e) {
      console.error(e);
    }
  };

  const deleteDiagnostic = async (taskId) => {
    if (!confirm("Êtes-vous sûr de vouloir supprimer définitivement cet examen ?")) return;
    try {
      const res = await fetch(`${backendUrl}/api/diagnostic/delete/${taskId}`, {
        method: 'DELETE',
        headers: {
          'Authorization': `Bearer ${token}`
        }
      });
      if (res.ok) {
        setPatients(prev => {
          const updated = prev.filter(p => p.task_id !== taskId);
          updateStats(updated);
          return updated;
        });
        if (selectedPatient?.task_id === taskId) {
          setSelectedPatient(null);
        }
      } else {
        const errData = await res.json();
        alert(`Erreur: ${errData.error}`);
      }
    } catch (e) {
      console.error(e);
      alert("Impossible de se connecter au serveur.");
    }
  };

  // Public/Auth Check for specific pages
  if (!token) {
    return (
      <div className="flex min-h-screen bg-[#0b111e] text-slate-100 items-center justify-center font-sans px-4">
        <div className="glass w-full max-w-md p-8 rounded-3xl glow-teal space-y-6">
          <div className="flex flex-col items-center gap-3 text-center">
            <div className="p-3 bg-teal-600/20 text-teal-400 rounded-2xl">
              <Activity className="w-10 h-10" />
            </div>
            <div>
              <h1 className="font-extrabold text-2xl tracking-tight text-white">OPTHIA V2</h1>
              <p className="text-xs text-teal-400 font-semibold uppercase tracking-wider">Accès Clinicien Sécurisé</p>
            </div>
          </div>

          {authError && (
            <div className="bg-rose-500/10 border border-rose-500/25 p-3.5 rounded-xl text-rose-400 text-sm font-semibold flex items-center gap-2">
              <AlertCircle className="w-4 h-4" /> {authError}
            </div>
          )}

          <form onSubmit={handleAuthSubmit} className="space-y-4">
            {authMode === 'register' && (
              <div>
                <label className="text-xs font-bold text-slate-400 block mb-1">Nom complet</label>
                <input 
                  type="text" 
                  required
                  placeholder="Dr. Alexandre Martin"
                  value={authForm.name}
                  onChange={(e) => setAuthForm({ ...authForm, name: e.target.value })}
                  className="w-full bg-slate-900 border border-slate-700 rounded-xl p-3 text-sm focus:outline-none focus:border-teal-500"
                />
              </div>
            )}
            
            <div>
              <label className="text-xs font-bold text-slate-400 block mb-1">Adresse email</label>
              <input 
                type="email" 
                required
                placeholder="nom@ophtia.local"
                value={authForm.email}
                onChange={(e) => setAuthForm({ ...authForm, email: e.target.value })}
                className="w-full bg-slate-900 border border-slate-700 rounded-xl p-3 text-sm focus:outline-none focus:border-teal-500"
              />
            </div>

            <div>
              <label className="text-xs font-bold text-slate-400 block mb-1">Mot de passe</label>
              <input 
                type="password" 
                required
                placeholder="••••••••"
                value={authForm.password}
                onChange={(e) => setAuthForm({ ...authForm, password: e.target.value })}
                className="w-full bg-slate-900 border border-slate-700 rounded-xl p-3 text-sm focus:outline-none focus:border-teal-500"
              />
            </div>

            {authMode === 'register' && (
              <div>
                <label className="text-xs font-bold text-slate-400 block mb-1">Rôle</label>
                <select 
                  value={authForm.role}
                  onChange={(e) => setAuthForm({ ...authForm, role: e.target.value })}
                  className="w-full bg-slate-900 border border-slate-700 rounded-xl p-3 text-sm focus:outline-none focus:border-teal-500"
                >
                  <option value="doctor">Médecin / Ophtalmologue</option>
                  <option value="admin">Administrateur Technique</option>
                </select>
              </div>
            )}

            <button 
              type="submit"
              className="w-full bg-teal-600 hover:bg-teal-500 text-white font-bold py-3 rounded-xl transition-all button-press shadow-lg shadow-teal-600/10"
            >
              {authMode === 'login' ? 'Se connecter' : 'Créer un compte'}
            </button>
          </form>

          <div className="text-center text-xs text-slate-400">
            {authMode === 'login' ? (
              <p>
                Vous n'avez pas de compte ?{' '}
                <button onClick={() => setAuthMode('register')} className="text-teal-400 hover:underline font-bold">
                  Créer un compte
                </button>
              </p>
            ) : (
              <p>
                Déjà inscrit ?{' '}
                <button onClick={() => setAuthMode('login')} className="text-teal-400 hover:underline font-bold">
                  Se connecter
                </button>
              </p>
            )}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen bg-[#0b111e] text-slate-100 overflow-hidden font-sans">
      
      {/* Sidebar navigation */}
      <aside className="w-72 bg-[#131c2e] border-r border-slate-800 flex flex-col justify-between">
        <div>
          <div className="p-6 flex items-center gap-3 border-b border-slate-800">
            <div className="w-12 h-12 rounded-xl overflow-hidden bg-teal-950/20 border border-teal-500/20 flex items-center justify-center">
              <img src="/logo.png" alt="OPTHIA CLINIC Logo" className="w-full h-full object-cover scale-110" />
            </div>
            <div>
              <h1 className="font-bold text-lg tracking-tight text-white leading-none">OPTHIA</h1>
              <span className="text-[10px] text-teal-400 font-bold tracking-wider uppercase">CLINIC V2 PRO</span>
            </div>
          </div>
          
          <nav className="p-4 space-y-2">
            <button 
              onClick={() => setActiveTab('dashboard')}
              className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-semibold transition-all button-press ${activeTab === 'dashboard' ? 'bg-teal-600 text-white shadow-lg shadow-teal-600/20' : 'text-slate-400 hover:text-white hover:bg-slate-800/50'}`}
            >
              <Users className="w-5 h-5" />
              <span>Tableau de bord</span>
            </button>
            <button 
              onClick={() => setActiveTab('import')}
              className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-semibold transition-all button-press ${activeTab === 'import' ? 'bg-teal-600 text-white shadow-lg shadow-teal-600/20' : 'text-slate-400 hover:text-white hover:bg-slate-800/50'}`}
            >
              <UploadCloud className="w-5 h-5" />
              <span>Import en Lot (Batch)</span>
            </button>
            <button 
              onClick={() => {
                setActiveTab('phonecapture');
                setCapturedPhoto(null);
              }}
              className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-semibold transition-all button-press ${activeTab === 'phonecapture' ? 'bg-teal-600 text-white shadow-lg shadow-teal-600/20' : 'text-slate-400 hover:text-white hover:bg-slate-800/50'}`}
            >
              <Camera className="w-5 h-5" />
              <span>Capture d'images</span>
            </button>
            
            {user?.role === 'admin' && (
              <button 
                onClick={() => setActiveTab('export')}
                className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-semibold transition-all button-press ${activeTab === 'export' ? 'bg-teal-600 text-white shadow-lg shadow-teal-600/20' : 'text-slate-400 hover:text-white hover:bg-slate-800/50'}`}
              >
                <Database className="w-5 h-5" />
                <span>Réapprentissage</span>
              </button>
            )}

            <button 
              onClick={() => setActiveTab('profile')}
              className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-semibold transition-all button-press ${activeTab === 'profile' ? 'bg-teal-600 text-white shadow-lg shadow-teal-600/20' : 'text-slate-400 hover:text-white hover:bg-slate-800/50'}`}
            >
              <User className="w-5 h-5" />
              <span>Mon Compte</span>
            </button>
          </nav>
        </div>

        <div className="p-6 border-t border-slate-800 space-y-4">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-full bg-teal-600/25 border border-teal-500/20 flex items-center justify-center font-bold text-teal-400 text-sm">
              {user?.name ? user.name.charAt(0).toUpperCase() : '?'}
            </div>
            <div className="overflow-hidden">
              <span className="text-sm font-semibold text-white block truncate">{user?.name}</span>
              <span className="text-xs text-slate-500 block truncate">{user?.email}</span>
            </div>
          </div>
          <button 
            onClick={logout}
            className="w-full flex items-center justify-center gap-2 py-2 px-3 bg-rose-500/10 hover:bg-rose-600 text-rose-400 hover:text-white font-bold rounded-xl transition-all text-xs button-press"
          >
            <LogOut className="w-3.5 h-3.5" /> Déconnexion
          </button>
        </div>
      </aside>

      {/* Main Workspace */}
      <main className="flex-1 flex flex-col overflow-hidden bg-slate-950/20">
        
        {/* Top Navbar */}
        <header className="h-20 bg-[#131c2e] border-b border-slate-800 flex items-center justify-between px-8">
          <div>
            <h2 className="text-xl font-bold text-white">
              {activeTab === 'dashboard' && "Suivi des Patients"}
              {activeTab === 'import' && "Importation par Lot"}
              {activeTab === 'export' && "Espace Réapprentissage Admin"}
              {activeTab === 'profile' && "Paramètres de Compte"}
            </h2>
            <p className="text-xs text-slate-400">Ophtalmologie assistée par Intelligence Artificielle</p>
          </div>
          
          {batchProgress && batchProgress.status === 'processing' && (
            <div className="flex items-center gap-4 bg-slate-800/50 px-4 py-2 rounded-xl border border-slate-700">
              <Loader2 className="w-4 h-4 text-teal-400 animate-spin" />
              <div className="text-xs">
                <span className="font-semibold text-white">Analyse en cours : </span>
                <span>{batchProgress.completed}/{batchProgress.total} patients</span>
              </div>
              <div className="w-24 bg-slate-700 h-2 rounded-full overflow-hidden">
                <div 
                  className="bg-teal-500 h-full transition-all duration-300"
                  style={{ width: `${(batchProgress.completed / batchProgress.total) * 100}%` }}
                />
              </div>
            </div>
          )}
        </header>

        {/* Content Section */}
        <div className="flex-1 overflow-y-auto p-8">

          {/* TAB 1: DASHBOARD */}
          {activeTab === 'dashboard' && (
            <div className="space-y-8">
              
              {/* Stats Overview */}
              <div className="grid grid-cols-4 gap-6">
                <div className="glass p-6 rounded-2xl glow-teal">
                  <div className="flex justify-between items-start">
                    <span className="text-xs uppercase tracking-wider text-slate-400 font-bold">Total Diagnostiqués</span>
                    <Users className="w-5 h-5 text-teal-400" />
                  </div>
                  <h3 className="text-3xl font-extrabold mt-4 text-white">{stats.total}</h3>
                </div>
                <div className="glass p-6 rounded-2xl border-l-4 border-rose-500">
                  <div className="flex justify-between items-start">
                    <span className="text-xs uppercase tracking-wider text-slate-400 font-bold">Cas Suspects / Positifs</span>
                    <AlertCircle className="w-5 h-5 text-rose-400" />
                  </div>
                  <h3 className="text-3xl font-extrabold mt-4 text-white">{stats.positive}</h3>
                </div>
                <div className="glass p-6 rounded-2xl border-l-4 border-emerald-500">
                  <div className="flex justify-between items-start">
                    <span className="text-xs uppercase tracking-wider text-slate-400 font-bold">Cas Sains / Négatifs</span>
                    <CheckCircle className="w-5 h-5 text-emerald-400" />
                  </div>
                  <h3 className="text-3xl font-extrabold mt-4 text-white">{stats.negative}</h3>
                </div>
                <div className="glass p-6 rounded-2xl">
                  <div className="flex justify-between items-start">
                    <span className="text-xs uppercase tracking-wider text-slate-400 font-bold">Validés par Médecin</span>
                    <Database className="w-5 h-5 text-teal-400" />
                  </div>
                  <h3 className="text-3xl font-extrabold mt-4 text-white">{stats.validated}</h3>
                </div>
              </div>

              {/* Patient List Table */}
              <div className="glass rounded-2xl overflow-hidden">
                <div className="p-6 border-b border-slate-800 flex justify-between items-center">
                  <h3 className="font-bold text-lg text-white">Registre des examens</h3>
                  <div className="flex items-center gap-2">
                    <button 
                      onClick={() => {
                        if (!patients.length) return alert("Aucun diagnostic à exporter.");
                        const headers = ["Nom Patient", "Fichier", "Pathologie", "Resultat IA", "Confiance", "Validation Expert", "Classe Confirmee", "Commentaire Clinique", "Date"];
                        const rows = patients.map(p => [
                          p.patient_name,
                          p.filename,
                          p.model_type === 'rd' ? 'Retinopathie Diabetique' : 'Glaucome',
                          p.result?.prediction_class === 1 ? 'POSITIF' : 'NEGATIF',
                          `${((p.result?.probability || 0) * 100).toFixed(1)}%`,
                          p.validated_by_admin ? 'VALIDE' : 'EN ATTENTE',
                          p.confirmed_class !== undefined ? p.confirmed_class : '',
                          (p.admin_comments || '').replace(/,/g, ' '),
                          p.timestamp
                        ]);
                        const csvContent = "data:text/csv;charset=utf-8,\uFEFF" 
                          + [headers.join(","), ...rows.map(e => e.join(","))].join("\n");
                        const encodedUri = encodeURI(csvContent);
                        const link = document.createElement("a");
                        link.setAttribute("href", encodedUri);
                        link.setAttribute("download", `opthia_diagnostic_registry_${new Date().toISOString().split('T')[0]}.csv`);
                        document.body.appendChild(link);
                        link.click();
                        document.body.removeChild(link);
                      }}
                      className="flex items-center gap-1.5 px-3 py-1.5 bg-teal-600/20 hover:bg-teal-600 border border-teal-500/30 text-teal-400 hover:text-white rounded-lg text-xs font-bold transition-all button-press"
                    >
                      <FileSpreadsheet className="w-3.5 h-3.5" /> Exporter en CSV
                    </button>
                    <button 
                      onClick={fetchPatients} 
                      className="p-2 hover:bg-slate-800 rounded-lg text-slate-400 hover:text-white transition-colors"
                    >
                      <RefreshCw className="w-4 h-4" />
                    </button>
                  </div>
                </div>
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="bg-slate-900/50 text-slate-400 text-xs font-semibold uppercase tracking-wider border-b border-slate-800">
                      <th className="p-4">Nom du Patient</th>
                      <th className="p-4">Pathologie</th>
                      <th className="p-4">Confiance</th>
                      <th className="p-4">Diagnostic</th>
                      <th className="p-4">Validation</th>
                      <th className="p-4">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/60 text-sm">
                    {patients.map((patient) => (
                      <tr 
                        key={patient.task_id}
                        className="hover:bg-slate-900/30 transition-colors"
                      >
                        <td className="p-4 font-bold text-white">{patient.patient_name}</td>
                        <td className="p-4">
                          <span className="px-3 py-1 rounded-full text-xs font-medium bg-slate-800 text-slate-300">
                            {patient.model_type === 'rd' ? 'Rétinopathie Diabétique' : 'Glaucome'}
                          </span>
                        </td>
                        <td className="p-4 font-mono">
                          {((patient.result?.probability || 0) * 100).toFixed(1)}%
                        </td>
                        <td className="p-4">
                          {patient.result?.prediction_class === 1 ? (
                            <span className="text-rose-400 font-semibold flex items-center gap-1.5">
                              <AlertCircle className="w-4 h-4" /> POSITIF
                            </span>
                          ) : (
                            <span className="text-emerald-400 font-semibold flex items-center gap-1.5">
                              <CheckCircle className="w-4 h-4" /> NÉGATIF
                            </span>
                          )}
                        </td>
                        <td className="p-4">
                          {patient.validated_by_admin ? (
                            <span className="text-xs bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 px-2 py-0.5 rounded">
                              Validé
                            </span>
                          ) : (
                            <span className="text-xs bg-amber-500/10 text-amber-400 border border-amber-500/30 px-2 py-0.5 rounded">
                              En attente
                            </span>
                          )}
                        </td>
                        <td className="p-4 flex items-center gap-3">
                          <button 
                            onClick={async () => {
                              setSelectedPatient(patient);
                              setViewMode('raw');
                              setChatHistory([]);
                              try {
                                const chatRes = await fetch(`${backendUrl}/api/chat/${patient.task_id}`, {
                                  headers: {
                                    'Authorization': `Bearer ${token}`
                                  }
                                });
                                if (chatRes.ok) {
                                  const history = await chatRes.json();
                                  setChatHistory(history);
                                }
                              } catch (e) {
                                console.error("Erreur de recuperation de l'historique du chat:", e);
                              }
                            }}
                            className="text-teal-400 hover:text-teal-300 font-semibold flex items-center gap-1 transition-colors button-press"
                          >
                            Consulter <ChevronRight className="w-4 h-4" />
                          </button>
                          <button 
                            onClick={() => deleteDiagnostic(patient.task_id)}
                            className="text-rose-500 hover:text-rose-400 font-semibold text-xs transition-colors button-press"
                          >
                            Supprimer
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* TAB 2: IMPORT BATCH */}
          {activeTab === 'import' && (
            <div className="max-w-3xl mx-auto space-y-8">
              <div className="glass p-8 rounded-2xl text-center space-y-6 glow-teal">
                <div className="w-16 h-16 bg-teal-600/10 text-teal-400 rounded-2xl flex items-center justify-center mx-auto border border-teal-500/20">
                  <UploadCloud className="w-10 h-10" />
                </div>
                
                <div className="space-y-2">
                  <h3 className="text-xl font-bold text-white">Importer des examens par lot</h3>
                  <p className="text-sm text-slate-400 max-w-md mx-auto">
                    Glissez-déposez ou sélectionnez un dossier complet d'images de fond d'œil pour lancer le traitement simultané de plusieurs patients (jusqu'à 100).
                  </p>
                </div>

                <div className="border-2 border-dashed border-slate-700 hover:border-teal-500 rounded-xl p-8 transition-colors cursor-pointer relative bg-slate-900/20">
                  <input 
                    type="file" 
                    multiple
                    onChange={handleFolderUpload}
                    className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                  />
                  <span className="text-sm text-slate-300 font-semibold">
                    Cliquez pour choisir les fichiers de fond d'œil
                  </span>
                </div>
              </div>

              {uploadQueue.length > 0 && (
                <div className="glass p-6 rounded-2xl space-y-6">
                  <div className="flex justify-between items-center">
                    <h4 className="font-bold text-white">Liste des examens à soumettre</h4>
                    <span className="text-xs px-2.5 py-1 bg-slate-800 text-slate-400 rounded-full font-bold">
                      {uploadQueue.length} Fichiers
                    </span>
                  </div>

                  <div className="max-h-60 overflow-y-auto space-y-2">
                    {uploadQueue.map(item => (
                      <div key={item.id} className="flex justify-between items-center p-3 bg-slate-900/40 rounded-xl border border-slate-800/80">
                        <div className="flex items-center gap-3">
                          <div className="text-xs bg-slate-800 text-slate-400 p-2 rounded">
                            {item.filename.split('.').pop().toUpperCase()}
                          </div>
                          <div>
                            <span className="text-sm font-semibold text-white block">{item.patient_name}</span>
                            <span className="text-xs text-slate-500">{item.filename}</span>
                          </div>
                        </div>
                        
                        <select 
                          value={item.model_type}
                          onChange={(e) => {
                            setUploadQueue(prev => prev.map(p => p.id === item.id ? { ...p, model_type: e.target.value } : p));
                          }}
                          className="bg-slate-800 border border-slate-700 text-xs rounded px-2.5 py-1 text-slate-300"
                        >
                          <option value="rd">Rétinopathie Diabétique</option>
                          <option value="glaucoma">Glaucome</option>
                        </select>
                      </div>
                    ))}
                  </div>

                  <button 
                    onClick={submitBatchUpload}
                    disabled={isUploading}
                    className="w-full bg-teal-600 hover:bg-teal-500 disabled:bg-slate-800 text-white font-bold py-3 rounded-xl transition-all button-press flex justify-center items-center gap-2 shadow-lg shadow-teal-600/10"
                  >
                    {isUploading ? (
                      <>
                        <Loader2 className="w-5 h-5 animate-spin" /> Soumission en cours...
                      </>
                    ) : (
                      <>
                        <CheckCircle className="w-5 h-5" /> Lancer l'analyse du lot
                      </>
                    )}
                  </button>
                </div>
              )}
            </div>
          )}

          {/* TAB: PHONECAPTURE CAMERA MODULE */}
          {activeTab === 'phonecapture' && (
            <div className="max-w-2xl mx-auto space-y-8">
              <div className="glass p-8 rounded-3xl glow-teal space-y-6">
                <div className="flex justify-between items-center border-b border-slate-800 pb-4">
                  <div>
                    <h3 className="text-xl font-bold text-white">Capture d'images DICOM</h3>
                    <p className="text-xs text-slate-400">Capturez et convertissez directement des images médicales en format DICOM</p>
                  </div>
                  <Camera className="w-8 h-8 text-teal-400" />
                </div>

                {/* Camera Viewfinder */}
                {!capturedPhoto ? (
                  <div className="space-y-4">
                    <div className="relative rounded-2xl overflow-hidden bg-black aspect-video flex items-center justify-center border border-slate-800">
                      {cameraStream ? (
                        <video 
                          ref={(video) => {
                            if (video && video.srcObject !== cameraStream) {
                              video.srcObject = cameraStream;
                              video.play().catch(err => console.error("Video play error:", err));
                            }
                          }}
                          className="w-full h-full object-cover"
                          playsInline
                          muted
                        />
                      ) : (
                        <div className="text-center p-6 space-y-4">
                          <p className="text-sm text-slate-500">L'appareil photo n'est pas activé.</p>
                          <button
                            onClick={async () => {
                              try {
                                // Essayer d'abord la caméra arrière (environnement)
                                try {
                                  const stream = await navigator.mediaDevices.getUserMedia({
                                    video: { facingMode: 'environment' }
                                  });
                                  setCameraStream(stream);
                                } catch (innerErr) {
                                  // Repli automatique sur n'importe quelle caméra disponible (webcam PC, etc.)
                                  const stream = await navigator.mediaDevices.getUserMedia({
                                    video: true
                                  });
                                  setCameraStream(stream);
                                }
                              } catch (e) {
                                alert("Erreur d'accès à la caméra : " + e.message);
                              }
                            }}
                            className="bg-teal-600 hover:bg-teal-500 text-white font-bold px-5 py-2.5 rounded-xl text-xs transition-all"
                          >
                            Activer l'objectif
                          </button>
                        </div>
                      )}
                    </div>
                    {cameraStream && (
                      <div className="flex justify-center gap-4">
                        <button
                          onClick={() => {
                            const video = document.querySelector('video');
                            if (video) {
                              const canvas = document.createElement('canvas');
                              canvas.width = video.videoWidth;
                              canvas.height = video.videoHeight;
                              const ctx = canvas.getContext('2d');
                              ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
                              const dataUrl = canvas.toDataURL('image/jpeg');
                              setCapturedPhoto(dataUrl);
                              
                              // Stop video stream
                              cameraStream.getTracks().forEach(track => track.stop());
                              setCameraStream(null);
                            }
                          }}
                          className="bg-rose-600 hover:bg-rose-500 text-white font-bold px-6 py-3 rounded-xl text-sm transition-all shadow-lg shadow-rose-600/10 flex items-center gap-2"
                        >
                          Prendre la photo
                        </button>
                        <button
                          onClick={() => {
                            cameraStream.getTracks().forEach(track => track.stop());
                            setCameraStream(null);
                          }}
                          className="bg-slate-800 hover:bg-slate-700 text-slate-300 font-bold px-5 py-3 rounded-xl text-sm transition-all"
                        >
                          Désactiver
                        </button>
                      </div>
                    )}
                  </div>
                ) : (
                  // Patient Association Form
                  <div className="space-y-6">
                    <div className="relative rounded-2xl overflow-hidden bg-slate-900 border border-slate-800 p-2">
                      <img src={capturedPhoto} alt="Captured fundus" className="max-h-60 mx-auto rounded-xl object-contain" />
                      <button
                        onClick={() => {
                          setCapturedPhoto(null);
                          // Réactiver directement la caméra
                          navigator.mediaDevices.getUserMedia({
                            video: { facingMode: 'environment' }
                          }).then(stream => setCameraStream(stream))
                            .catch(err => console.error("Camera reload error:", err));
                        }}
                        className="absolute top-4 right-4 bg-slate-950/80 hover:bg-slate-900 text-slate-300 px-3 py-1.5 rounded-lg text-xs transition-colors border border-slate-800"
                      >
                        Reprendre la photo
                      </button>
                    </div>

                    <div className="space-y-4">
                      <h4 className="font-bold text-sm text-teal-400 uppercase tracking-wider">Métadonnées médicales du Patient</h4>
                      <div className="grid grid-cols-2 gap-4">
                        <div>
                          <label className="text-xs font-bold text-slate-400 block mb-1">Nom complet</label>
                          <input 
                            type="text" 
                            placeholder="ex: Jean Dupont"
                            value={dicomPatient.name}
                            onChange={(e) => setDicomPatient({ ...dicomPatient, name: e.target.value })}
                            className="w-full bg-slate-900 border border-slate-700 rounded-xl p-3 text-sm text-white focus:outline-none focus:border-teal-500"
                          />
                        </div>
                        <div>
                          <label className="text-xs font-bold text-slate-400 block mb-1">Identifiant Unique (ID Patient)</label>
                          <input 
                            type="text" 
                            placeholder="ex: PAT8234"
                            value={dicomPatient.id}
                            onChange={(e) => setDicomPatient({ ...dicomPatient, id: e.target.value })}
                            className="w-full bg-slate-900 border border-slate-700 rounded-xl p-3 text-sm text-white focus:outline-none focus:border-teal-500"
                          />
                        </div>
                      </div>
                      <div>
                        <label className="text-xs font-bold text-slate-400 block mb-1">Date de Naissance (Format JJMMAAAA)</label>
                        <input 
                          type="text" 
                          placeholder="ex: 12051978"
                          maxLength={8}
                          value={dicomPatient.birthDate}
                          onChange={(e) => setDicomPatient({ ...dicomPatient, birthDate: e.target.value })}
                          className="w-full bg-slate-900 border border-slate-700 rounded-xl p-3 text-sm text-white focus:outline-none focus:border-teal-500"
                        />
                      </div>

                      <button
                        onClick={async () => {
                          if (!dicomPatient.name || !dicomPatient.id) {
                            return alert("Veuillez saisir au moins le nom et l'identifiant du patient.");
                          }
                          setIsSendingDicom(true);
                          try {
                            const res = await fetch(`${backendUrl}/api/dicom/upload`, {
                              method: 'POST',
                              headers: {
                                'Content-Type': 'application/json',
                                'Authorization': `Bearer ${token}`
                              },
                              body: JSON.stringify({
                                patient_name: dicomPatient.name,
                                patient_id: dicomPatient.id,
                                birth_date: dicomPatient.birthDate || '19800101',
                                image_base64: capturedPhoto
                              })
                            });
                            if (res.ok) {
                              alert("✓ Image convertie en DICOM et archivée dans le PACS Orthanc avec succès !");
                              setCapturedPhoto(null);
                              setDicomPatient({ name: '', id: '', birthDate: '' });
                              fetchPatients(); // Rafraîchir
                            } else {
                              const err = await res.json();
                              alert(`Erreur: ${err.error}`);
                            }
                          } catch (e) {
                            console.error(e);
                            alert("Erreur de connexion avec le PACS.");
                          } finally {
                            setIsSendingDicom(false);
                          }
                        }}
                        disabled={isSendingDicom}
                        className="w-full bg-teal-600 hover:bg-teal-500 disabled:bg-slate-800 text-white font-bold py-3 rounded-xl transition-all button-press flex justify-center items-center gap-2 shadow-lg shadow-teal-600/10 text-sm"
                      >
                        {isSendingDicom ? (
                          <>
                            <Loader2 className="w-5 h-5 animate-spin" /> Analyse en cours...
                          </>
                        ) : (
                          <>
                            Lancer l'examen (Sauvegarder DICOM)
                          </>
                        )}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* TAB 3: EXPORT TRAINING */}
          {activeTab === 'export' && user.role === 'admin' && (
            <div className="max-w-2xl mx-auto space-y-8">
              <div className="glass p-8 rounded-2xl space-y-6">
                <div className="w-12 h-12 bg-amber-500/10 text-amber-400 border border-amber-500/20 rounded-xl flex items-center justify-center">
                  <Lock className="w-6 h-6" />
                </div>
                
                <div className="space-y-2">
                  <h3 className="text-lg font-bold text-white">Espace Réapprentissage Local</h3>
                  <p className="text-sm text-slate-400">
                    Pour améliorer les performances du modèle, vous pouvez exporter les données qui ont été manuellement vérifiées et validées par un spécialiste de santé.
                  </p>
                </div>

                <div className="p-4 bg-slate-900/60 rounded-xl border border-slate-800/80 space-y-4">
                  <h4 className="font-bold text-sm text-white uppercase tracking-wider text-slate-300">Status du Dataset</h4>
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <span className="text-xs text-slate-500 block">Données disponibles</span>
                      <span className="text-xl font-bold text-white">{stats.validated} Diagnostics validés</span>
                    </div>
                    <div>
                      <span className="text-xs text-slate-500 block">Format d'exportation</span>
                      <span className="text-sm font-semibold text-teal-400">ZIP (Images + Annotations JSON)</span>
                    </div>
                  </div>
                </div>

                <a 
                  href={`${backendUrl}/api/admin/export`}
                  download
                  className={`w-full flex justify-center items-center gap-2 bg-amber-600 hover:bg-amber-500 text-white font-bold py-3 rounded-xl transition-all button-press shadow-lg shadow-amber-600/10 ${stats.validated === 0 ? 'pointer-events-none opacity-50 bg-slate-800' : ''}`}
                >
                  <Download className="w-5 h-5" /> Exporter le Dataset de Réentraînement
                </a>
              </div>
            </div>
          )}

          {/* TAB 4: PROFILE ACCOUNT MANAGEMENT */}
          {activeTab === 'profile' && (
            <div className="max-w-xl mx-auto space-y-8">
              <div className="glass p-8 rounded-3xl glow-teal space-y-6">
                <div className="flex items-center gap-4">
                  <div className="p-3 bg-teal-600/20 text-teal-400 rounded-2xl">
                    <User className="w-6 h-6" />
                  </div>
                  <div>
                    <h3 className="text-lg font-bold text-white">Gestion de mon Compte</h3>
                    <p className="text-xs text-slate-400">Mettez à jour vos identifiants ou votre mot de passe</p>
                  </div>
                </div>

                {profileMessage && (
                  <div className={`p-4 rounded-xl border text-sm font-semibold flex items-center gap-2 ${profileMessage.startsWith('Erreur') ? 'bg-rose-500/10 border-rose-500/20 text-rose-400' : 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400'}`}>
                    {profileMessage.startsWith('Erreur') ? <AlertCircle className="w-4 h-4" /> : <ShieldCheck className="w-4 h-4" />}
                    {profileMessage}
                  </div>
                )}

                <form onSubmit={handleProfileUpdate} className="space-y-4">
                  <div>
                    <label className="text-xs font-bold text-slate-400 block mb-1">Nom complet</label>
                    <input 
                      type="text" 
                      required
                      value={profileForm.name}
                      onChange={(e) => setProfileForm({ ...profileForm, name: e.target.value })}
                      className="w-full bg-slate-900 border border-slate-700 rounded-xl p-3 text-sm text-white focus:outline-none focus:border-teal-500"
                    />
                  </div>

                  <div>
                    <label className="text-xs font-bold text-slate-400 block mb-1">Adresse email (Non modifiable)</label>
                    <input 
                      type="email" 
                      disabled
                      value={user.email}
                      className="w-full bg-slate-900/50 border border-slate-800 text-slate-500 rounded-xl p-3 text-sm cursor-not-allowed"
                    />
                  </div>

                  <div>
                    <label className="text-xs font-bold text-slate-400 block mb-1">Rôle clinicien</label>
                    <div className="px-3 py-1 bg-slate-900 border border-slate-800 rounded-xl inline-block text-xs font-bold text-teal-400 uppercase tracking-wider">
                      {user.role === 'admin' ? 'Administrateur Technique' : 'Médecin / Clinicien'}
                    </div>
                  </div>

                  <div className="border-t border-slate-800 pt-4">
                    <label className="text-xs font-bold text-slate-400 block mb-1">Nouveau mot de passe (Laisser vide pour ne pas changer)</label>
                    <input 
                      type="password" 
                      placeholder="••••••••"
                      value={profileForm.password}
                      onChange={(e) => setProfileForm({ ...profileForm, password: e.target.value })}
                      className="w-full bg-slate-900 border border-slate-700 rounded-xl p-3 text-sm text-white focus:outline-none focus:border-teal-500"
                    />
                  </div>

                  <button 
                    type="submit"
                    className="w-full bg-teal-600 hover:bg-teal-500 text-white font-bold py-3 rounded-xl transition-all button-press flex justify-center items-center gap-2"
                  >
                    <KeyRound className="w-4 h-4" /> Mettre à jour mon compte
                  </button>
                </form>
              </div>
            </div>
          )}

        </div>
      </main>

      {/* Patient Detail Modal (Translucid blur effect) */}
      {selectedPatient && (
        <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex justify-center items-center p-8">
          <div className="glass w-full max-w-6xl h-[85vh] rounded-3xl overflow-hidden flex flex-col">
            
            <div className="p-6 border-b border-slate-800 flex justify-between items-center bg-[#131c2e]/80">
              <div>
                <h3 className="text-xl font-bold text-white">{selectedPatient.patient_name}</h3>
                <span className="text-xs text-slate-400">{selectedPatient.filename}</span>
              </div>
              <button 
                onClick={() => setSelectedPatient(null)}
                className="text-slate-400 hover:text-white font-bold text-lg p-2 hover:bg-slate-800 rounded-lg"
              >
                Fermer
              </button>
            </div>

            <div className="flex-1 flex overflow-hidden">
              
              {/* Left Column: Image and diagnostic results */}
              <div className="w-1/2 p-8 overflow-y-auto space-y-6 border-r border-slate-800">
                <div className="flex justify-between items-center bg-slate-900/60 p-2 rounded-xl border border-slate-800">
                  <span className="text-xs font-bold text-slate-300 ml-2">Mode de visualisation</span>
                  <div className="flex gap-2">
                    {selectedPatient.model_type === 'glaucoma' ? (
                      <>
                        <button 
                          onClick={() => setViewMode('raw')}
                          className={`px-3 py-1 rounded-lg text-xs font-bold transition-all ${viewMode === 'raw' ? 'bg-teal-600 text-white' : 'text-slate-400 hover:text-white'}`}
                        >
                          Image brute
                        </button>
                        <button 
                          onClick={() => setViewMode('cropped')}
                          className={`px-3 py-1 rounded-lg text-xs font-bold transition-all ${viewMode === 'cropped' ? 'bg-teal-600 text-white' : 'text-slate-400 hover:text-white'}`}
                        >
                          Image crop
                        </button>
                        <button 
                          onClick={() => setViewMode('gradcam')}
                          className={`px-3 py-1 rounded-lg text-xs font-bold transition-all ${viewMode === 'gradcam' ? 'bg-teal-600 text-white' : 'text-slate-400 hover:text-white'}`}
                        >
                          Grad-CAM Heatmap
                        </button>
                      </>
                    ) : (
                      <>
                        <button 
                          onClick={() => setViewMode('raw')}
                          className={`px-3 py-1 rounded-lg text-xs font-bold transition-all ${viewMode === 'raw' ? 'bg-teal-600 text-white' : 'text-slate-400 hover:text-white'}`}
                        >
                          Image brute
                        </button>
                        <button 
                          onClick={() => setViewMode('gradcam')}
                          className={`px-3 py-1 rounded-lg text-xs font-bold transition-all ${viewMode === 'gradcam' ? 'bg-teal-600 text-white' : 'text-slate-400 hover:text-white'}`}
                        >
                          Grad-CAM Heatmap
                        </button>
                      </>
                    )}
                  </div>
                </div>
 
                <div 
                  onClick={() => setIsFullScreenImage(true)}
                  className="relative rounded-2xl overflow-hidden bg-slate-950 aspect-video flex items-center justify-center border border-slate-800 cursor-zoom-in group"
                >
                  {viewMode === 'gradcam' ? (
                    selectedPatient.result?.grad_cam ? (
                      <img 
                        src={selectedPatient.result.grad_cam} 
                        alt="Grad-CAM diagnostic" 
                        className="max-h-full object-contain transition-transform duration-300 group-hover:scale-[1.02]"
                      />
                    ) : (
                      <div className="text-slate-500 text-sm flex flex-col items-center gap-2">
                        <AlertCircle className="w-8 h-8" />
                        <span>Heatmap Grad-CAM non disponible</span>
                      </div>
                    )
                  ) : viewMode === 'cropped' ? (
                    selectedPatient.image_cropped_base64 ? (
                      <img 
                        src={selectedPatient.image_cropped_base64} 
                        alt="Cropped eye disc" 
                        className="max-h-full object-contain transition-transform duration-300 group-hover:scale-[1.02]"
                      />
                    ) : (
                      <div className="text-slate-500 text-sm flex flex-col items-center gap-2">
                        <AlertCircle className="w-8 h-8" />
                        <span>Image recadrée non disponible</span>
                      </div>
                    )
                  ) : (
                    selectedPatient.image_base64 ? (
                      <img 
                        src={selectedPatient.image_base64} 
                        alt="Original eye fundus" 
                        className="max-h-full object-contain transition-transform duration-300 group-hover:scale-[1.02]"
                      />
                    ) : (
                      <div className="text-slate-500 text-sm flex flex-col items-center gap-2">
                        <AlertCircle className="w-8 h-8" />
                        <span>Image d'origine non disponible</span>
                      </div>
                    )
                  )}
                  <div className="absolute bottom-3 right-3 bg-black/75 px-2.5 py-1 rounded-lg text-[10px] font-bold text-teal-400 opacity-0 group-hover:opacity-100 transition-opacity flex items-center gap-1.5 border border-teal-500/20">
                    🔍 Cliquer pour zoomer
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="bg-[#1b263b]/40 p-4 rounded-xl border border-slate-800">
                    <span className="text-xs text-slate-400 block uppercase font-semibold">Résultat</span>
                    <span className={`text-lg font-bold block mt-1 ${selectedPatient.result?.prediction_class === 1 ? 'text-rose-400' : 'text-emerald-400'}`}>
                      {selectedPatient.result?.prediction_class === 1 ? '⚠️ Positif' : '✅ Négatif'}
                    </span>
                  </div>
                  <div className="bg-[#1b263b]/40 p-4 rounded-xl border border-slate-800">
                    <span className="text-xs text-slate-400 block uppercase font-semibold">Taux de Confiance</span>
                    <span className="text-lg font-bold text-white block mt-1">
                      {((selectedPatient.result?.probability || 0) * 100).toFixed(2)}%
                    </span>
                  </div>
                </div>

                <div className="bg-[#1b263b]/20 p-4 rounded-xl border border-slate-850 text-sm text-slate-300">
                  <h4 className="font-bold text-white mb-2">Recommandation médicale</h4>
                  <p>{selectedPatient.result?.recommendation}</p>
                </div>

                {/* Admin Validation Form */}
                <div className="border-t border-slate-800 pt-6 space-y-4">
                  <h4 className="font-bold text-white text-sm">Validation Expert (Administrateur)</h4>
                  <div className="flex gap-4">
                    <button 
                      onClick={() => confirmLabel(0)}
                      className="flex-1 bg-emerald-600/20 hover:bg-emerald-600 text-emerald-400 hover:text-white font-bold py-2 rounded-lg border border-emerald-500/30 transition-all button-press text-sm"
                    >
                      Valider comme sain (0)
                    </button>
                    <button 
                      onClick={() => confirmLabel(1)}
                      className="flex-1 bg-rose-600/20 hover:bg-rose-600 text-rose-400 hover:text-white font-bold py-2 rounded-lg border border-rose-500/30 transition-all button-press text-sm"
                    >
                      Valider comme pathologique (1)
                    </button>
                  </div>
                  <textarea 
                    placeholder="Ajouter des notes cliniques pour le dataset..."
                    value={adminComments}
                    onChange={(e) => setAdminComments(e.target.value)}
                    className="w-full bg-slate-900 border border-slate-700 rounded-xl p-3 text-sm text-slate-200 focus:outline-none focus:border-teal-500"
                    rows={2}
                  />
                  {selectedPatient.validated_by_admin && (
                    <div className="text-xs text-emerald-400 bg-emerald-500/5 p-2.5 rounded border border-emerald-500/10">
                      ✓ Labellisation confirmée par l'expert : classe {selectedPatient.confirmed_class}
                    </div>
                  )}
                </div>
              </div>

              {/* Right Column: Chatbot with Gemini */}
              <div className="w-1/2 flex flex-col bg-slate-950/40">
                <div className="p-4 border-b border-slate-800 bg-slate-900/40 flex items-center gap-2">
                  <MessageSquare className="w-5 h-5 text-teal-400" />
                  <span className="font-bold text-sm text-white">Assistant Médical Gemini</span>
                </div>

                <div className="flex-1 p-6 overflow-y-auto space-y-4">
                  <div className="flex gap-3">
                    <div className="w-8 h-8 rounded-full bg-teal-600/15 flex items-center justify-center text-teal-400 text-xs font-bold border border-teal-500/20 shrink-0">
                      IA
                    </div>
                    <div className="bg-slate-800/80 p-3 rounded-2xl text-sm max-w-[85%] text-slate-200">
                      Bonjour, je suis votre assistant médical basé sur Gemini. Vous pouvez me poser des questions sur les symptômes observés sur le fond d'œil du patient ou me demander d'expliquer ce diagnostic.
                    </div>
                  </div>

                  {/* Custom Markdown Helper to render Gemini text beautifully */}
                  {(() => {
                    window.renderMessageText = (text) => {
                      if (!text) return "";
                      
                      // Convert line breaks
                      let formatted = text.split("\n").map((line, i) => {
                        let content = line.trim();
                        
                        // Bold parsing **text** -> <strong>text</strong>
                        content = content.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
                        
                        // Bullet point list items
                        if (content.startsWith("* ")) {
                          return <li key={i} className="ml-4 list-disc my-1 text-slate-200" dangerouslySetInnerHTML={{ __html: content.substring(2) }} />;
                        }
                        
                        // Numbered list items
                        if (/^\d+\.\s/.test(content)) {
                          const parts = content.split(/^\d+\.\s/);
                          return <li key={i} className="ml-4 list-decimal my-1 text-slate-200" dangerouslySetInnerHTML={{ __html: parts[1] }} />;
                        }
                        
                        // Header tags
                        if (content.startsWith("### ")) {
                          return <h4 key={i} className="text-sm font-extrabold text-teal-400 mt-4 mb-1 uppercase tracking-wider" dangerouslySetInnerHTML={{ __html: content.substring(4) }} />;
                        }
                        
                        return content ? <p key={i} className="mb-2 text-slate-200 leading-relaxed" dangerouslySetInnerHTML={{ __html: content }} /> : <div key={i} className="h-2" />;
                      });
                      
                      return formatted;
                    };
                  })()}

                  {chatHistory.map((msg, index) => (
                    <div key={index} className={`flex gap-3 ${msg.role === 'user' ? 'justify-end' : ''}`}>
                      {msg.role !== 'user' && (
                        <div className="w-8 h-8 rounded-full bg-teal-600/15 flex items-center justify-center text-teal-400 text-xs font-bold border border-teal-500/20 shrink-0">
                          IA
                        </div>
                      )}
                      <div className={`p-4 rounded-2xl text-sm max-w-[85%] leading-relaxed ${msg.role === 'user' ? 'bg-teal-600 text-white rounded-tr-none' : 'bg-slate-800/80 text-slate-200 rounded-tl-none border border-slate-700/30'}`}>
                        {msg.role === 'user' ? msg.text : window.renderMessageText(msg.text)}
                      </div>
                    </div>
                  ))}

                  {isChatLoading && (
                    <div className="flex gap-3">
                      <div className="w-8 h-8 rounded-full bg-teal-600/15 flex items-center justify-center text-teal-400 text-xs font-bold border border-teal-500/20 shrink-0">
                        IA
                      </div>
                      <div className="bg-slate-800/50 p-3 rounded-2xl text-sm text-slate-400 flex items-center gap-2">
                        <Loader2 className="w-4 h-4 animate-spin text-teal-400" /> Analyse de votre message...
                      </div>
                    </div>
                  )}
                </div>

                <form onSubmit={handleSendMessage} className="p-4 border-t border-slate-800 bg-slate-900/30 flex gap-2">
                  <input 
                    type="text" 
                    placeholder="Poser une question médicale à l'IA..."
                    value={chatMessage}
                    onChange={(e) => setChatMessage(e.target.value)}
                    className="flex-1 bg-slate-900 border border-slate-700 rounded-xl px-4 text-sm text-white focus:outline-none focus:border-teal-500"
                  />
                  <button 
                    type="submit"
                    className="p-3 bg-teal-600 hover:bg-teal-500 text-white rounded-xl transition-all button-press flex justify-center items-center shadow-lg shadow-teal-600/10"
                  >
                    <Send className="w-4 h-4" />
                  </button>
                </form>
              </div>

            </div>

          </div>
        </div>
      )}

      {/* Image Fullscreen Zoom Modal */}
      {isFullScreenImage && selectedPatient && (
        <div 
          onClick={() => setIsFullScreenImage(false)}
          className="fixed inset-0 z-50 bg-black/95 backdrop-blur-md flex flex-col justify-center items-center p-4 cursor-zoom-out"
        >
          <div className="absolute top-4 right-4 flex gap-4 z-55" onClick={e => e.stopPropagation()}>
            {selectedPatient.model_type === 'glaucoma' ? (
              <>
                <button 
                  onClick={() => setViewMode('raw')}
                  className={`px-4 py-2 rounded-xl text-xs font-bold transition-all ${viewMode === 'raw' ? 'bg-teal-600 text-white' : 'bg-slate-800 text-slate-400 border border-slate-700'}`}
                >
                  Image brute
                </button>
                <button 
                  onClick={() => setViewMode('cropped')}
                  className={`px-4 py-2 rounded-xl text-xs font-bold transition-all ${viewMode === 'cropped' ? 'bg-teal-600 text-white' : 'bg-slate-800 text-slate-400 border border-slate-700'}`}
                >
                  Image crop
                </button>
                <button 
                  onClick={() => setViewMode('gradcam')}
                  className={`px-4 py-2 rounded-xl text-xs font-bold transition-all ${viewMode === 'gradcam' ? 'bg-teal-600 text-white' : 'bg-slate-800 text-slate-400 border border-slate-700'}`}
                >
                  Grad-CAM
                </button>
              </>
            ) : (
              <>
                <button 
                  onClick={() => setViewMode('raw')}
                  className={`px-4 py-2 rounded-xl text-xs font-bold transition-all ${viewMode === 'raw' ? 'bg-teal-600 text-white' : 'bg-slate-800 text-slate-400 border border-slate-700'}`}
                >
                  Image brute
                </button>
                <button 
                  onClick={() => setViewMode('gradcam')}
                  className={`px-4 py-2 rounded-xl text-xs font-bold transition-all ${viewMode === 'gradcam' ? 'bg-teal-600 text-white' : 'bg-slate-800 text-slate-400 border border-slate-700'}`}
                >
                  Grad-CAM
                </button>
              </>
            )}
            <button 
              onClick={() => setIsFullScreenImage(false)}
              className="bg-rose-600/80 hover:bg-rose-500 text-white font-bold px-4 py-2 rounded-xl text-xs transition-colors"
            >
              Fermer la loupe
            </button>
          </div>

          <div className="max-w-5xl max-h-[85vh] w-full flex items-center justify-center p-2" onClick={e => e.stopPropagation()}>
            {viewMode === 'gradcam' ? (
              selectedPatient.result?.grad_cam ? (
                <img 
                  src={selectedPatient.result.grad_cam} 
                  alt="Grad-CAM diagnostic Fullscreen" 
                  className="max-w-full max-h-[80vh] object-contain rounded-2xl border border-slate-800 shadow-2xl"
                />
              ) : (
                <div className="text-slate-500 text-sm flex flex-col items-center gap-2">
                  <AlertCircle className="w-8 h-8" />
                  <span>Heatmap Grad-CAM non disponible</span>
                </div>
              )
            ) : viewMode === 'cropped' ? (
              selectedPatient.image_cropped_base64 ? (
                <img 
                  src={selectedPatient.image_cropped_base64} 
                  alt="Cropped eye disc Fullscreen" 
                  className="max-w-full max-h-[80vh] object-contain rounded-2xl border border-slate-800 shadow-2xl"
                />
              ) : (
                <div className="text-slate-500 text-sm flex flex-col items-center gap-2">
                  <AlertCircle className="w-8 h-8" />
                  <span>Image recadrée non disponible</span>
                </div>
              )
            ) : (
              selectedPatient.image_base64 ? (
                <img 
                  src={selectedPatient.image_base64} 
                  alt="Original eye fundus Fullscreen" 
                  className="max-w-full max-h-[80vh] object-contain rounded-2xl border border-slate-800 shadow-2xl"
                />
              ) : (
                <div className="text-slate-500 text-sm flex flex-col items-center gap-2">
                  <AlertCircle className="w-8 h-8" />
                  <span>Image d'origine non disponible</span>
                </div>
              )
            )}
          </div>
          <p className="text-xs text-slate-500 mt-4 select-none">
            Visualisation en mode {viewMode === 'gradcam' ? 'Grad-CAM' : viewMode === 'cropped' ? 'Image crop' : "Image brute (Originale)"} — Cliquer n'importe où pour fermer
          </p>
        </div>
      )}

    </div>
  );
}
