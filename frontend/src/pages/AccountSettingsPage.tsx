import React, { useState, useRef, useEffect } from 'react';
import Sidebar from '../components/Sidebar';
import { useAuth } from '../context/AuthContext';

const AccountSettingsPage: React.FC = () => {
  const [activeTab, _setActiveTab] = useState('user');
  const [isEditing, setIsEditing] = useState(false);

  const { user, signOut } = useAuth();
  const userEmail = user?.email || '';
  const userInitial = (user?.email?.charAt(0) ?? 'U').toUpperCase();

  // Keys scoped to user email so different users don't share saved data
  const key = (field: string) => `user_${userEmail}_${field}`;

  // Load from localStorage or use defaults — updated when auth resolves
  const [profileImage, setProfileImage] = useState<string | null>(null);
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [phoneNumber, setPhoneNumber] = useState('');
  const [aadhar, setAadhar] = useState('4281 9021 5521');
  const [country, setCountry] = useState('India');

  // Sync from localStorage once the user is known (auth resolves async)
  useEffect(() => {
    if (!userEmail) return;
    const defaultName = userEmail.split('@')[0];
    setProfileImage(localStorage.getItem(key('profileImage')));
    setFirstName(localStorage.getItem(key('firstName')) || defaultName);
    setLastName(localStorage.getItem(key('lastName')) || 'Doe');
    setPhoneNumber(localStorage.getItem(key('phone')) || '+91 9876543210');
    setAadhar(localStorage.getItem(key('aadhar')) || '4281 9021 5521');
    setCountry(localStorage.getItem(key('country')) || 'India');
  }, [userEmail]);

  // Edit states for user form (separate so Cancel doesn't lose saved values)
  const [editFirstName, setEditFirstName] = useState('');
  const [editLastName, setEditLastName] = useState('');
  const [editPhoneNumber, setEditPhoneNumber] = useState('');

  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleImageUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      const file = e.target.files[0];
      const reader = new FileReader();
      reader.onloadend = () => {
        const base64String = reader.result as string;
        setProfileImage(base64String);
        localStorage.setItem(key('profileImage'), base64String);
      };
      reader.readAsDataURL(file);
    }
  };

  const handleUploadClick = () => fileInputRef.current?.click();

  const handleRemoveImage = () => {
    setProfileImage(null);
    localStorage.removeItem(key('profileImage'));
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const toggleEdit = () => {
    if (!isEditing) {
      setEditFirstName(firstName);
      setEditLastName(lastName);
      setEditPhoneNumber(phoneNumber);
    }
    setIsEditing(!isEditing);
  };

  const handleSave = () => {
    setFirstName(editFirstName);
    setLastName(editLastName);
    setPhoneNumber(editPhoneNumber);
    localStorage.setItem(key('firstName'), editFirstName);
    localStorage.setItem(key('lastName'), editLastName);
    localStorage.setItem(key('phone'), editPhoneNumber);
    setIsEditing(false);
  };

  const handleSaveVerification = () => {
    localStorage.setItem(key('aadhar'), aadhar);
    localStorage.setItem(key('country'), country);
    alert('Verification Details Saved Successfully!');
  };

  const inputClass = "w-full px-3 py-2.5 bg-[#FAFAFA] border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-100 focus:border-blue-500 transition-all text-sm";
  const readonlyClass = "w-full px-3 py-2.5 text-sm font-medium";

  return (
    <div className="flex h-screen overflow-hidden bg-groww-bg-primary">
      {/* Left Sidebar */}
      <Sidebar />

      {/* Main content area */}
      <main className="flex-1 overflow-y-auto overflow-x-hidden">
        <div className="p-4 sm:p-6 lg:p-8 max-w-4xl mx-auto w-full">
          <h1 className="text-xl sm:text-2xl font-bold text-groww-text-primary mb-4 sm:mb-6">Account Settings</h1>

          <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-4 sm:p-6 lg:p-8 w-full">

            {/* ── Profile Header ─────────────────────────────────── */}
            <div className="flex flex-col sm:flex-row sm:justify-between sm:items-start gap-4 mb-6 sm:mb-8">
              {/* Avatar + upload buttons */}
              <div className="flex flex-col sm:flex-row items-start sm:items-center gap-4">
                <div className="relative shrink-0">
                  {profileImage ? (
                    <img src={profileImage} alt="Profile" className="w-20 h-20 sm:w-24 sm:h-24 rounded-full object-cover shadow-md" />
                  ) : (
                    <div className="w-20 h-20 sm:w-24 sm:h-24 rounded-full bg-gradient-to-br from-blue-400 to-indigo-600 flex items-center justify-center text-white text-2xl sm:text-3xl font-bold shadow-md">
                      {userInitial}
                    </div>
                  )}
                  <input type="file" ref={fileInputRef} onChange={handleImageUpload} accept="image/*" className="hidden" />
                  <button
                    onClick={handleUploadClick}
                    className="absolute bottom-0 right-0 w-7 h-7 sm:w-8 sm:h-8 bg-blue-700 rounded-full flex items-center justify-center text-white border-2 border-white shadow-sm hover:bg-blue-800 transition-colors"
                  >
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                      <polyline points="17 8 12 3 7 8"/>
                      <line x1="12" y1="3" x2="12" y2="15"/>
                    </svg>
                  </button>
                </div>

                <div className="flex flex-row sm:flex-row gap-2 flex-wrap">
                  <button onClick={handleUploadClick} className="px-4 py-2 bg-blue-700 text-white rounded-lg text-sm font-medium hover:bg-blue-800 transition-colors shadow-sm whitespace-nowrap">
                    Upload New
                  </button>
                  <button onClick={handleRemoveImage} className="px-4 py-2 bg-gray-100 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-200 transition-colors border border-gray-200 whitespace-nowrap">
                    Delete avatar
                  </button>
                </div>
              </div>

              {/* Edit button */}
              <button
                onClick={toggleEdit}
                className="flex items-center gap-2 text-blue-700 font-semibold hover:text-blue-800 bg-[#F4F6FF] px-4 py-2 rounded-lg transition-colors self-start sm:self-auto text-sm"
              >
                {isEditing ? (
                  <>Cancel Edit</>
                ) : (
                  <>
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                      <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                    </svg>
                    Edit
                  </>
                )}
              </button>
            </div>

            {/* ── Personal Info Fields ────────────────────────────── */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 sm:gap-6 mb-6 sm:mb-8">
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">First Name <span className="text-red-500">*</span></label>
                {isEditing ? (
                  <input type="text" value={editFirstName} onChange={(e) => setEditFirstName(e.target.value)} className={inputClass} />
                ) : (
                  <div className={`${readonlyClass} text-gray-800`}>{firstName || '—'}</div>
                )}
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">Last Name <span className="text-red-500">*</span></label>
                {isEditing ? (
                  <input type="text" value={editLastName} onChange={(e) => setEditLastName(e.target.value)} className={inputClass} />
                ) : (
                  <div className={`${readonlyClass} text-gray-800`}>{lastName || '—'}</div>
                )}
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">Email</label>
                {isEditing ? (
                  <input type="email" value={userEmail} readOnly className={`${inputClass} text-gray-400 cursor-not-allowed`} />
                ) : (
                  <div className={`${readonlyClass} text-gray-500`}>{userEmail || '—'}</div>
                )}
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">Mobile Number <span className="text-red-500">*</span></label>
                {isEditing ? (
                  <input type="text" value={editPhoneNumber} onChange={(e) => setEditPhoneNumber(e.target.value)} className={inputClass} />
                ) : (
                  <div className={`${readonlyClass} text-gray-800`}>{phoneNumber || '—'}</div>
                )}
              </div>
            </div>

            {/* ── Verification Details (always visible) ──────────── */}
            <div className="mb-6 sm:mb-8">
              <h3 className="text-sm font-bold text-gray-700 mb-3 uppercase tracking-wide border-b border-gray-100 pb-2">Verification Details</h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 sm:gap-6">
                <div>
                  <label className="block text-xs font-semibold text-gray-600 mb-1">Aadhar Identification Number</label>
                  <input
                    type="text"
                    value={aadhar}
                    onChange={(e) => setAadhar(e.target.value)}
                    className={inputClass}
                    placeholder="1234 5678 9012"
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-gray-600 mb-1">Country</label>
                  <select
                    value={country}
                    onChange={(e) => setCountry(e.target.value)}
                    className={`${inputClass} appearance-none`}
                  >
                    <option value="India">India</option>
                    <option value="USA">United States</option>
                    <option value="UK">United Kingdom</option>
                  </select>
                </div>
                <div className="sm:col-span-2">
                  <button onClick={handleSaveVerification} className="px-5 py-2.5 bg-blue-700 text-white rounded-lg text-sm font-medium hover:bg-blue-800 transition-colors shadow-sm">
                    Save Verification Details
                  </button>
                </div>
              </div>
            </div>

            {/* ── Notification Toggles ────────────────────────────── */}
            <div className="mb-6 sm:mb-8">
              <h3 className="text-sm font-bold text-gray-700 mb-3 uppercase tracking-wide border-b border-gray-100 pb-2">Notifications</h3>
              <div className="space-y-3">
                <div className="p-4 bg-[#FAFAFA] rounded-xl border border-gray-200 flex justify-between items-center gap-4">
                  <div className="min-w-0">
                    <p className="font-semibold text-gray-800 text-sm">Email Notifications</p>
                    <p className="text-xs text-gray-500 mt-0.5">Receive alerts about your account via email.</p>
                  </div>
                  <div className="w-11 h-6 bg-blue-600 rounded-full relative cursor-pointer shrink-0">
                    <div className="w-4 h-4 bg-white rounded-full absolute top-1 right-1 shadow-sm"></div>
                  </div>
                </div>
                <div className="p-4 bg-[#FAFAFA] rounded-xl border border-gray-200 flex justify-between items-center gap-4">
                  <div className="min-w-0">
                    <p className="font-semibold text-gray-800 text-sm">SMS Notifications</p>
                    <p className="text-xs text-gray-500 mt-0.5">Get instant updates about trades on your phone.</p>
                  </div>
                  <div className="w-11 h-6 bg-gray-300 rounded-full relative cursor-pointer shrink-0">
                    <div className="w-4 h-4 bg-white rounded-full absolute top-1 left-1 shadow-sm"></div>
                  </div>
                </div>
              </div>
            </div>

            {/* ── Stocks Currently Held ───────────────────────────── */}
            <div className="mb-6 sm:mb-8">
              <h3 className="text-sm font-bold text-gray-700 mb-3 uppercase tracking-wide border-b border-gray-100 pb-2">Stocks Currently Held</h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {[
                  { name: 'Apple (AAPL)', shares: '12 Shares', pnl: '+$142.50', positive: true },
                  { name: 'Tesla (TSLA)', shares: '5 Shares', pnl: '-$24.10', positive: false },
                  { name: 'Alphabet (GOOGL)', shares: '8 Shares', pnl: '+$56.20', positive: true },
                ].map((stock) => (
                  <div key={stock.name} className="bg-[#FAFAFA] p-4 rounded-xl border border-gray-200 flex items-center justify-between hover:border-blue-300 transition-colors">
                    <div className="min-w-0 mr-2">
                      <p className="font-bold text-gray-800 text-sm truncate">{stock.name}</p>
                      <p className="text-xs text-gray-500 mt-0.5">{stock.shares}</p>
                    </div>
                    <p className={`text-sm font-semibold shrink-0 ${stock.positive ? 'text-green-600' : 'text-red-500'}`}>{stock.pnl}</p>
                  </div>
                ))}
              </div>
            </div>

            {/* ── Footer actions ──────────────────────────────────── */}
            <div className="pt-5 border-t border-gray-100 flex flex-col sm:flex-row justify-between items-stretch sm:items-center gap-3">
              {isEditing ? (
                <button onClick={handleSave} className="px-6 py-2.5 bg-blue-700 text-white font-bold rounded-lg hover:bg-blue-800 transition-colors shadow-sm text-sm">
                  Save Changes
                </button>
              ) : (
                <div />
              )}
              <button onClick={signOut} className="px-6 py-2.5 bg-red-50 text-red-600 font-bold rounded-lg hover:bg-red-100 transition-colors text-sm sm:ml-auto">
                Log Out
              </button>
            </div>

          </div>
        </div>
      </main>
    </div>
  );
};

export default AccountSettingsPage;
